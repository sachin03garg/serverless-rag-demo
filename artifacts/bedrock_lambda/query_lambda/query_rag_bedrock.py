import boto3
from os import getenv
from opensearchpy import OpenSearch, RequestsHttpConnection, exceptions
from requests_aws4auth import AWS4Auth
from io import BytesIO
import requests
import json
from decimal import Decimal
import logging
import re
import base64

from agents.retriever_agent import fetch_data, fetch_data_v2, classify_and_translation_request
from prompt_utils import AGENT_MAP, get_system_prompt, agent_execution_step, rag_chat_bot_prompt
from prompt_utils import casual_prompt, get_classification_prompt, RESERVED_TAGS
from prompt_utils import get_can_the_orchestrator_answer_prompt
from prompt_utils import sentiment_prompt, generate_claude_3_ocr_prompt
from prompt_utils import pii_redact_prompt
from agent_executor_utils import agent_executor
from pypdf import PdfReader

bedrock_client = boto3.client('bedrock-runtime')
embed_model_id = getenv("EMBED_MODEL_ID", "amazon.titan-embed-image-v1")
LOG = logging.getLogger()
LOG.setLevel(logging.INFO)
endpoint = getenv("OPENSEARCH_VECTOR_ENDPOINT", "https://admin:P@@dummy-amazonaws.com:443")

SAMPLE_DATA_DIR = getenv("SAMPLE_DATA_DIR", "/var/task")
INDEX_NAME = getenv("VECTOR_INDEX_NAME", "sample-embeddings-store-dev")
wss_url = getenv("WSS_URL", "WEBSOCKET_URL_MISSING")
rest_api_url = getenv("REST_ENDPOINT_URL", "REST_URL_MISSING")
is_rag_enabled = getenv("IS_RAG_ENABLED", 'yes')
s3_bucket_name = getenv("S3_BUCKET_NAME", "S3_BUCKET_NAME_MISSING")
websocket_client = boto3.client('apigatewaymanagementapi', endpoint_url=wss_url)
lambda_client = boto3.client('lambda')

credentials = boto3.Session().get_credentials()
service = 'aoss'
region = getenv("REGION", "us-east-1")
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                   region, service, session_token=credentials.token)

# Agent code start
list_of_tools_specs = []
tool_names = []
tool_descriptions = []
                    
def query_rag_no_agent(user_input, query_vector_db, language, model_id, is_hybrid_search, connect_id):
    global rag_chat_bot_prompt
    final_prompt = rag_chat_bot_prompt
    chat_input = json.loads(user_input)
    LOG.debug(f'Chat history {chat_input}')
    can_invoke_model = False

    user_chat_history = ''
    for chat in chat_input:
        if 'role' in chat and chat['role'] == 'user':
            for message in chat['content']:
                if message['type'] == 'text':
                    user_conv_wo_context = re.sub('<context>.*?</context>','Context redacted',message['text'], flags=re.DOTALL)
                    user_chat_history += 'user: ' + user_conv_wo_context + '. '
                elif message['type'] == 'image' and 'source' in message and 'partial_s3_key' in message['source']:
                    s3_key = f"bedrock/data/{message['source']['partial_s3_key']}"
                    del message['source']
                    message['type']='text'
                    message['text'] = f"content at S3 location: {s3_key}"
                    user_chat_history += 'user:' + message['text']
        elif 'role' in chat and chat['role'] == 'assistant':
            for message in chat['content']:
                if message['type'] == 'text':
                    user_chat_history += 'assistant: ' + message['text'] + '. '
    # First step is classification
    context = None
    classify_translate_json = classify_and_translation_request(user_chat_history)

    if 'QUERY_TYPE' in  classify_translate_json and classify_translate_json['QUERY_TYPE'] == 'RETRIEVAL':
        if 'TRANSLATED_QUERY' in classify_translate_json:
            reformulated_q = classify_translate_json['TRANSLATED_QUERY']
            proper_nouns = []
            if 'PROPER_NOUNS' in classify_translate_json:
                proper_nouns = classify_translate_json['PROPER_NOUNS']
            if query_vector_db == 'yes':
                context = fetch_data_v2(reformulated_q, proper_nouns, is_hybrid_search)
    
    if 'role' in chat_input[-1] and 'user' == chat_input[-1]['role']:
        can_invoke_model=True
        for text_inputs in chat_input[-1]['content']:
            if text_inputs['type'] == 'text' and '<user-question>' not in text_inputs['text']:
                text_inputs['text'] =  f'<user-question> {text_inputs["text"]} </user-question>'
                if 'QUERY_TYPE' in  classify_translate_json and classify_translate_json['QUERY_TYPE'] == 'RETRIEVAL' and context is not None:
                    text_inputs['text'] = f"""<context> {context} </context> {text_inputs['text']} """
                elif 'QUERY_TYPE' in  classify_translate_json and classify_translate_json['QUERY_TYPE'] == 'CASUAL':
                    final_prompt = rag_chat_bot_prompt + casual_prompt
                break
            elif text_inputs['type'] == 'image':
                if 'source' in text_inputs and 'partial_s3_key' in text_inputs['source']:
                    s3_key = f"bedrock/data/{text_inputs['source']['partial_s3_key']}"
                    LOG.debug(f'Fetch document from S3 {s3_key}')
                    encoded_file = base64.b64encode(get_file_from_s3(s3_bucket_name, s3_key))
                    del text_inputs['source']['partial_s3_key']
                    del text_inputs['source']['file_extension']
                    text_inputs['source']['data'] = encoded_file.decode('utf-8')

    if can_invoke_model:
        prompt_template = {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 70000,
                        "system": final_prompt + f'. You will always respond in {language} language',
                        "messages": chat_input
        }

        LOG.info(f'chat prompt_template {prompt_template}')
        invoke_model(0, prompt_template, connect_id, True, model_id)
                    

def query_agents(agent_type, user_input, connect_id):
    master_orchestrator(agent_type, json.loads(user_input), connect_id)
    # return success_response(connect_id, "success")

# The Orchestrator Agent
def master_orchestrator(agent_type: str, chat_input, connect_id):
    done = False
    # Clean up chat_input remove presigned length URLs when processing the next user-request
    for chat in chat_input:
        if 'content' in chat:
            cntnt = []
            for msg in chat['content']:
                if 'text' in msg:
                    cntnt = msg['text']
                    if any(ele in cntnt for ele in RESERVED_TAGS):
                        for tag in RESERVED_TAGS:
                            last_half = ''
                            first_half = ''
                            # Do not change the order
                            if  '</' in tag:
                                last_half = cntnt.split(tag)[1]
                            else:
                                first_half = cntnt.split(tag)[0]
                            msg['text'] = first_half + '(S3).' + last_half
                
                        

                        
    # Orchestrator classifies the problem
    classify_prompt, output_agent, output_tags = get_classification_prompt(agent_type)
    websocket_send(connect_id, {"intermediate_execution": "Hang in there, generating results", "done": done})
    agent_name = agent_executor(classify_prompt, chat_input, output_agent, output_tags, False)
    websocket_send(connect_id, {"intermediate_execution": f"Hang in there, current agent:{agent_name}", "done": done})
    # Orchestrator decides which agent should handle the problem
    # Orchestrator injects methods associated with that agent into the prompt
    LOG.info(f'method=master_orchestrator, chat_history={chat_input}')
    system_prompt = get_system_prompt(agent_name)
    
    prompt_template= {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 10000,
                        "system": system_prompt,
                        "messages": chat_input
                    }
    
    prompt_flow = []
    prompt_flow.extend(chat_input)
    # Orchestrator executes the next step
    # Try to solve a user query in 5 steps
    for i in range(5):
        # To be displayed in StackTrace
        websocket_send(connect_id, {"intermediate_execution": f"Hang in there, current agent:{agent_name}, step: {i}", "done": done})
        LOG.debug(f"prompt_template {prompt_template}, iteration : {i}")
        step_plan = invoke_model(i, prompt_template, connect_id, False)
        websocket_send(connect_id, {"intermediate_execution": f"Hang in there, {agent_name} created a plan of action", "done": done})
        LOG.debug(f'Step {i} output {step_plan}')
        done, human_prompt, assistant_prompt, agent_name, contains_artifact = agent_execution_step(i, step_plan, prompt_flow)
        
        prompt_flow.append({"role":"assistant", "content": assistant_prompt })
        should_classify = True
        if not done and human_prompt is not None:
            prompt_flow.append({"role":"user", "content": human_prompt })
            websocket_send(connect_id, {"intermediate_execution": "Working on it", "done": done})
            reply = agent_executor(get_can_the_orchestrator_answer_prompt(), prompt_flow, "output", None, True)
            if '<can_answer>' in reply:
                should_classify = False
                done=True
                prompt_flow.append({"role":"assistant", "content": [{"type": "text", "text": reply.split('<can_answer>')[1].split('</can_answer>')[0]}]})
                websocket_send(connect_id, {"prompt_flow": prompt_flow, "done": done})
                return reply
        
        if done:
            # Check for RESERVED TAGS in assistant_prompt
            if contains_artifact:
                websocket_send(connect_id, {"intermediate_execution": f" Artifact created ..", "done": done})
            LOG.debug('Final answer from LLM:\n'+f'{assistant_prompt}')
            websocket_send(connect_id, {"prompt_flow": prompt_flow, "done": done})
            return assistant_prompt
        
        if should_classify:
            #if not done then find next agent
            agent_name = agent_executor(classify_prompt, prompt_flow, output_agent, output_tags, False)
            # if next agent could not be found return control back to the user
            if agent_name not in AGENT_MAP:
                LOG.warn(f'Agent name {agent_name} not in agent map. prompt_flow {prompt_flow}. Exit')
                done = True
                last_prmpt = prompt_flow[-1]
                generated_assist_prmpt = None
                if 'role' in last_prmpt and last_prmpt['role'] == 'user':
                    for content in last_prmpt['content']:
                        if content['type'] == 'text' and '<function_result>' in content['text']:
                            generated_assist_prmpt = content['text'].split('<function_result>')[1]
                            generated_assist_prmpt = generated_assist_prmpt.split('</function_result>')[0]
                            prompt_flow.append({"role":"assistant", "content": [{"type": "text", "text": generated_assist_prmpt}] })
                            websocket_send(connect_id, {"prompt_flow": prompt_flow, "done": done})
                            return assistant_prompt
            else:
                system_prompt = get_system_prompt(agent_name)
                prompt_template= {
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 10000,
                            "system": system_prompt,
                            "messages": prompt_flow
                        }
                websocket_send(connect_id, {"intermediate_execution": f"Hang in there, next Agent: {agent_name} assigned", "done": done})

        content = prompt_flow[-1]["content"]
        content.extend([{"type": "text", "text": "\n\n If you know the answer, say it. If not, what is the next step?"}])
        
    if not done:
        prompt_flow.append({"role":"assistant", "content": {type: "text", "text": "I apologize but I cant answer this question"} })
        websocket_send(connect_id, {"prompt_flow": prompt_flow, "done": True})
        return prompt_flow

def invoke_model(step_id, prompt, connect_id, send_on_socket=False, model_id = "anthropic.claude-3-sonnet-20240229-v1:0"):
    result = query_bedrock_claude3_model(step_id, model_id, prompt, connect_id, send_on_socket)
    return ''.join(result)

def query_bedrock_claude3_model(step_id, model, prompt, connect_id, send_on_socket=False):
    '''
       StepId and ConnectId can be used to stream data over the  socket
    '''
    cnk_str = []
    response = bedrock_client.invoke_model_with_response_stream(
        body=json.dumps(prompt),
        modelId=model,
        accept='application/json',
        contentType='application/json'
    )
    counter=0
    sent_ack = False
    for evt in response['body']:
        counter = counter + 1
        if 'chunk' in evt:
            chunk = evt['chunk']['bytes']
            chunk_json = json.loads(chunk.decode("UTF-8"))

            if chunk_json['type'] == 'content_block_delta' and chunk_json['delta']['type'] == 'text_delta':
                cnk_str.append(chunk_json['delta']['text'])
                if chunk_json['delta']['text'] and len((chunk_json['delta']['text']).split()) > 0:
                    if send_on_socket:
                        websocket_send(connect_id, { "text": chunk_json['delta']['text'] } )
        else:
            cnk_str.append(evt)
            break
        
        if 'internalServerException' in evt:
            result = evt['internalServerException']['message']
            websocket_send(connect_id, { "text": result } )
            break
        elif 'modelStreamErrorException' in evt:
            result = evt['modelStreamErrorException']['message']
            websocket_send(connect_id, { "text": result } )
            break
        elif 'throttlingException' in evt:
            result = evt['throttlingException']['message']
            websocket_send(connect_id, { "text": result } )
            break
        elif 'validationException' in evt:
            result = evt['validationException']['message']
            websocket_send(connect_id, { "text": result } )
            break

    if send_on_socket:
        websocket_send(connect_id, { "text": "ack-end-of-msg" } )

    return cnk_str


def store_image_in_s3(event):
    payload = json.loads(event['body'])
    file_encoded_data = payload['content']
    content_id = payload['id']
    file_extension = extract_file_extension(file_encoded_data)
    file_encoded_data = file_encoded_data[file_encoded_data.find(",") + 1:]
    file_content = base64.b64decode(file_encoded_data)
    s3_client = boto3.client('s3')
    s3_key = f"bedrock/data/{content_id}.{file_extension}"
    s3_client.put_object(Body=file_content, Bucket=s3_bucket_name, Key=s3_key)
    return http_success_response({'file_extension': file_extension, 'file_id': content_id, 'message': 'stored successfully'})

def create_presigned_post(event):
    # Generate a presigned S3 POST URL
    query_params = {}
    if 'queryStringParameters' in event:
        query_params = event['queryStringParameters']
    email_id = "empty_email_id"
    if 'requestContext' in event and 'authorizer' in event['requestContext']:
            if 'claims' in event['requestContext']['authorizer']:
                email_id = event['requestContext']['authorizer']['claims']['email']
    
    if 'file_extension' in query_params and 'file_name' in query_params:
        extension = query_params['file_extension']
        file_name = query_params['file_name']
        # Usecase could be index or ocr
        usecase_type = 'bedrock'
        if 'type' in query_params and query_params['type'] in ['index', 'ocr', 'bedrock']:
            usecase_type = query_params['type']
        # remove special characters from file name
        file_name = re.sub(r'[^a-zA-Z0-9_\-\.]','',file_name)

        session = boto3.Session()
        s3_client = session.client('s3', region_name=region)
        file_name = file_name.replace(' ', '_')
        s3_key = f"{usecase_type}/data/{file_name}.{extension}"
        # response = s3_client.generate_presigned_post(Bucket=s3_bucket_name,
        #                                       Key=s3_key,
        #                                       Fields=None,
        #                                       Conditions=[]
        #                                   )
        response = s3_client.generate_presigned_post(Bucket=s3_bucket_name,
                                            Key=s3_key,
                                            Fields={'x-amz-meta-email_id': email_id
                                                    },
                                            Conditions=[{'x-amz-meta-email_id': email_id}]
                                        )
        
        # 'x-amz-meta-email_id': email_id, 
        # The response contains the presigned URL and required fields
        return http_success_response(response)
    else:
        return http_failure_response('Missing file_extension field cannot generate signed url')

def extract_file_extension(base64_encoded_file):
    if base64_encoded_file.find(';') > -1:
        extension = base64_encoded_file.split(';')[0]
        return extension[extension.find('/') + 1:]
    # default to PNG if we are not able to extract extension or string is not bas64 encoded
    return 'png'

def handler(event, context):
    global region
    global websocket_client
    LOG.info(
        "---  Amazon Opensearch Serverless vector db example with Amazon Bedrock Models ---")
    LOG.info(f'event - {event}')

    if 'httpMethod' not in event and 'requestContext' in event:
    # this is a websocket request
        stage = event['requestContext']['stage']
        api_id = event['requestContext']['apiId']
        domain = f'{api_id}.execute-api.{region}.amazonaws.com'
        websocket_client = boto3.client('apigatewaymanagementapi', endpoint_url=f'https://{domain}/{stage}')

        connect_id = event['requestContext']['connectionId']
        routeKey = event['requestContext']['routeKey']

        if routeKey != '$connect':
            if 'body' in event:
                input_to_llm = json.loads(event['body'], strict=False)
                LOG.info('input_to_llm: ', input_to_llm)
                query = input_to_llm['query']
                language = 'english'
                if 'language' in input_to_llm:
                    language = input_to_llm['language']
                behaviour = input_to_llm['behaviour']
                if behaviour == 'advanced-agent':
                    query_agents(behaviour, query, connect_id)
                else:
                    query_vector_db = 'no'
                    if 'query_vectordb' in input_to_llm and input_to_llm['query_vectordb']=='yes':
                        query_vector_db='yes' 
                    if 'model_id' in input_to_llm:
                        model_id = input_to_llm['model_id']
                    is_hybrid_search = False
                    if 'is_hybrid_search' in input_to_llm and input_to_llm['is_hybrid_search'] == 'yes':
                        is_hybrid_search = True
                    query_rag_no_agent(query, query_vector_db, language, model_id, is_hybrid_search, connect_id)
        elif routeKey == '$connect':
            # TODO Add authentication of access token
            if 'access_token' in event['queryStringParameters']:
                headers = {'Content-Type': 'application/json', 'Authorization': event['queryStringParameters']['access_token'] }
                response = requests.get(f'{rest_api_url}connect-tracker', headers=headers, verify=False)
                if response.status_code == 200:
                    return {'statusCode': '200', 'body': 'Bedrock says hello' }
                else:
                    LOG.error(f'Response Error status_code: {response.status_code}, reason: {response.reason}')
                    return {'statusCode': f'{response.status_code}', 'body': f'Forbidden, {response.reason}' }
            return {'statusCode': 400, 'body': f'Forbidden, cant establish secure socket connection' }
                
            
    elif 'httpMethod' in event:
        api_map = {
            'POST/rag/file_data': lambda x: create_presigned_post(x)
        }
        http_method = event['httpMethod'] if 'httpMethod' in event else ''
        api_path = http_method + event['resource']
        try:
            if api_path in api_map:
                LOG.debug(f"method=handler , api_path={api_path}")
                return respond(None, api_map[api_path](event))
            else:
                LOG.info(f"error=api_not_found , api={api_path}")
                return respond(http_failure_response('api_not_supported'), None)
        except Exception:
            LOG.exception(f"error=error_processing_api, api={api_path}")
            return respond(http_success_response('system_exception'), None)

    return {'statusCode': '200', 'body': 'Bedrock says hello' }


def http_failure_response(error_message):
    return {"success": False, "errorMessage": error_message, "statusCode": "400"}

def http_success_response(result):
    return {"success": True, "result": result, "statusCode": "200"}

class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            if float(obj).is_integer():
                return int(float(obj))
            else:
                return float(obj)
        return super(CustomJsonEncoder, self).default(obj)

def respond(err, res=None):
    return {
        'statusCode': '400' if err else res['statusCode'],
        'body': json.dumps(err) if err else json.dumps(res, cls=CustomJsonEncoder),
        'headers': {
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/json",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Credentials": "*"
        },
    }

def failure_response(connect_id, error_message):
    global websocket_client
    err_msg = {"success": False, "errorMessage": error_message, "statusCode": "400"}
    websocket_send(connect_id, err_msg)


def extract_query_image_values(query):
    image_id = []
    user_query = []
    user_queries_data = json.loads(base64.b64decode(query))
    for user_query_type in user_queries_data:
        if 'type' in user_query_type and user_query_type['type'] == 'text':
            user_query.append(user_query_type['data'])
        elif 'type' in user_query_type and user_query_type['type'] == 'image':
            image_id.append(user_query_type['data'])
    return ' '.join(user_query), image_id


def get_contents(file_extension, file_bytes):
    content = ' '
    try:
        if file_extension in ['pdf']:
            textract_client = boto3.client('textract')
            response = textract_client.detect_document_text(Document={'Bytes': file_bytes})
            for block in response['Blocks']:
                if block['BlockType'] == 'LINE':
                    content = content + ' ' + block['Text']

        else:
            #file_extension in ['sql', 'txt', 'json', 'csv']:
            #if file_extension in ['csv', 'xls', 'xlsx']:
            content = file_bytes.decode()
    except Exception as e:
        LOG.error(f'Exception reading contents from file {e}')
    LOG.info(f'file-content {content}')
    return content

def get_file_from_s3(s3bucket, key):
    s3 = boto3.resource('s3')
    obj = s3.Object(s3bucket, key)
    file_bytes = obj.get()['Body'].read()
    LOG.debug(f'returns S3 encoded object from key {s3bucket}/{key}')
    return file_bytes

def success_response(connect_id, result):
    success_msg = {"success": True, "result": result, "statusCode": "200"}
    websocket_send(connect_id, success_msg)

def websocket_send(connect_id, message):
    global websocket_client
    global wss_url
    LOG.debug(f'WSS URL {wss_url}, connect_id {connect_id}, message {message}')
    response = websocket_client.post_to_connection(
                Data=base64.b64encode(json.dumps(message, indent=4).encode('utf-8')),
                ConnectionId=connect_id
            )

class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            if float(obj).is_integer():
                return int(float(obj))
            else:
                return float(obj)
        return super(CustomJsonEncoder, self).default(obj)
