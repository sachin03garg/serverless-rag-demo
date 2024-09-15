import {
  Button,
  Container,
  SpaceBetween,
  Spinner,
} from "@cloudscape-design/components";
import { useEffect, useLayoutEffect, useState } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { ChatScrollState } from "./chat-ui";
import { ChatMessage, ChatMessageType } from "./types";
import styles from "../../styles/chat-ui.module.scss";
import config from "../../config.json";

var ws = null; 
var agent_prompt_flow = []

export interface ChatUIInputPanelProps {
  inputPlaceholderText?: string;
  sendButtonText?: string;
  running?: boolean;
  messages?: ChatMessage[];
  onSendMessage?: (message: string, type: string) => void;
}

export default function ChatUIInputPanel(props: ChatUIInputPanelProps) {
  const [inputText, setInputText] = useState("");
  const socketUrl = config.websocketUrl;
  const [message, setMessage] = useState('');
  
  useEffect(() => {
    const onWindowScroll = () => {
      if (ChatScrollState.skipNextScrollEvent) {
        ChatScrollState.skipNextScrollEvent = false;
        return;
      }

      const isScrollToTheEnd =
        Math.abs(
          window.innerHeight +
            window.scrollY -
            document.documentElement.scrollHeight
        ) <= 10;

      if (!isScrollToTheEnd) {
        ChatScrollState.userHasScrolled = true;
      } else {
        ChatScrollState.userHasScrolled = false;
      }
    };

    window.addEventListener("scroll", onWindowScroll);

    return () => {
      window.removeEventListener("scroll", onWindowScroll);
    };
  }, []);

  useLayoutEffect(() => {
    if (ChatScrollState.skipNextHistoryUpdate) {
      ChatScrollState.skipNextHistoryUpdate = false;
      return;
    }

    if (!ChatScrollState.userHasScrolled && (props.messages ?? []).length > 0) {
      ChatScrollState.skipNextScrollEvent = true;
      window.scrollTo({
        top: document.documentElement.scrollHeight + 1000,
        behavior: "instant",
      });
    }
  }, [props.messages]);

  const onSendMessage = () => {
    ChatScrollState.userHasScrolled = false;
    props.onSendMessage?.(inputText, ChatMessageType.Human);
    setInputText("");

    const access_token = sessionStorage.getItem('accessToken');
    
    if (inputText.trim() !== '') {
      if ("WebSocket" in window) {
        agent_prompt_flow.push({ 'role': 'user', 'content': [{"type": "text", "text": inputText}] })
        if(ws==null || ws.readyState==3 || ws.readyState==2) {
          
          ws = new WebSocket(socketUrl);
          ws.onerror = function (event) {
            console.log(event);
          }
        } else {
          // query_vectordb allowed values -> yes/no
          ws.send(JSON.stringify({ query: btoa(unescape(JSON.stringify(agent_prompt_flow))) , behaviour: 'advanced-rag-agent', 'query_vectordb': 'yes', 'model_id': 'anthropic.claude-3-haiku-20240307-v1:0' }));
          
       }
        
        ws.onopen = () => {
          // query_vectordb allowed values -> yes/no
          ws.send(JSON.stringify({ query: btoa(unescape(JSON.stringify(agent_prompt_flow))), behaviour: 'advanced-rag-agent', 'query_vectordb': 'yes', 'model_id': 'anthropic.claude-3-haiku-20240307-v1:0'}));
          
        };
        var messages = ''
        ws.onmessage = (event) => {
          
          var response_details = JSON.parse(atob(event.data))
          if ('prompt_flow' in response_details) {
            var is_done = Boolean(response_details['done'])
            if (!is_done) {
              var thought = ''
              agent_prompt_flow = []
              for (var k = 0; k < response_details['prompt_flow'].length; k++) {
                var prompt_content_list = response_details['prompt_flow'][k]['content']
                var content = []
                for (var j = 0; j < prompt_content_list.length; j++) {
                  if ('text' in prompt_content_list[j]) {
                    content.push({ "type": "text", "text": prompt_content_list[j]['text'] })
                  } else {
                    content.push(prompt_content_list[j])
                  }
                }
                agent_prompt_flow.push({ "role": response_details['prompt_flow'][k]['role'], "content": content })
              }

              for (var i = 0; i < response_details['prompt_flow'].length; i++) {
                if ('content' in response_details['prompt_flow'][i]) {
                  var content_list = response_details['prompt_flow'][i]['content']
                  for (var j = 0; j < content_list.length; j++) {
                    if ('text' in content_list[j]) {
                      thought = thought + capitalizeFirstLetter(response_details['prompt_flow'][i]['role']) + ': ' + content_list[j]['text']
                    } else {
                      thought = thought +  capitalizeFirstLetter(response_details['prompt_flow'][i]['role']) + ': ' + content_list[j]
                    }
                  }
                }
              }
              messages = thought.replace('ack-end-of-string', '')
              props.onSendMessage?.(messages, ChatMessageType.AI);
            } else {
              if (response_details['prompt_flow'].length > 0) {
                var thought = ''
                var last_element = response_details['prompt_flow'][response_details['prompt_flow'].length - 1]
                if ('content' in last_element) {
                  var content_list = last_element['content']
                  var content = []
                  for (var j = 0; j < content_list.length; j++) {
                    if ('text' in content_list[j]) {
                      content.push({ "type": "text", "text": content_list[j]['text'] })
                      thought = thought + capitalizeFirstLetter(last_element['role']) + ': ' + content_list[j]['text']
                    } else {
                      content.push(content_list[j])
                      thought = thought + capitalizeFirstLetter(last_element['role']) + ': ' + content_list[j]
                    }
                  }
                  agent_prompt_flow.push({ "role": last_element['role'], "content": content })
                  
                  messages = thought.replace('ack-end-of-string', '')
                  props.onSendMessage?.(messages, ChatMessageType.AI);
                }
              }
            }

          }

          
          // if ('text' in chat_output) {
          //   messages += chat_output['text']
          //   if (messages.endsWith('ack-end-of-string')) {
          //     messages = messages.replace('ack-end-of-string', '')
          //     props.onSendMessage?.(messages, ChatMessageType.AI);
          //   }
          // } else {
          //   // Display errors
          //   props.onSendMessage?.(chat_output, ChatMessageType.AI);
          // }
          setMessage("");
        };

        ws.onclose = () => {
          console.log('WebSocket connection closed');
          agent_prompt_flow = []
        };

      } else {
        console.log('WebSocket is not supported by your browser.');
        agent_prompt_flow = []
      }
    }
  };

  function capitalizeFirstLetter(val) {
    return val.charAt(0).toUpperCase() + val.slice(1);
  }

  const onTextareaKeyDown = (
    event: React.KeyboardEvent<HTMLTextAreaElement>
  ) => {
    if (!props.running && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSendMessage();
    }
  };

  return (
    <SpaceBetween direction="vertical" size="l">
      <Container>
        <div className={styles.input_textarea_container}>
          <TextareaAutosize
            className={styles.input_textarea}
            maxRows={6}
            minRows={1}
            spellCheck={true}
            autoFocus
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={onTextareaKeyDown}
            value={inputText}
            placeholder={props.inputPlaceholderText ?? "Send a message"}
          />
          <div style={{ marginLeft: "8px" }}>
            <Button
              disabled={props.running || inputText.trim().length === 0}
              onClick={onSendMessage}
              iconAlign="right"
              iconName={!props.running ? "angle-right-double" : undefined}
              variant="primary"
            >
              {props.running ? (
                <>
                  Loading&nbsp;&nbsp;
                  <Spinner />
                </>
              ) : (
                <>{props.sendButtonText ?? "Send"}</>
              )}
            </Button>
          </div>
        </div>
      </Container>
    </SpaceBetween>
  );
}
