import {
  Container,
  ContentLayout,
  Box, Grid, SpaceBetween, Link
} from "@cloudscape-design/components";

export default function HomePage() {
  return (
    <ContentLayout
    defaultPadding
    style={{ backgroundColor: "#f9f9f9" }} // Light background color
  >
    <Box padding={{ vertical: "xxxl", horizontal: "l" }} textAlign="center">
      <Box fontSize="display-l" fontWeight="bold" variant="h1">
        IPS Contract Orchestrator
      </Box>
      <Box variant="p" color="text-body-secondary" margin={{ top: "xs", bottom: "l" }}>
        Chat with your documents and retrieve information using natural language.
      </Box>
    </Box>

    <Grid gridDefinition={[{ colspan: { default: 3, s: 4 } }, { colspan: { default: 9, s: 8 } }]}>
      <Box padding="s">
        <Box variant="h2">Use Cases</Box>
        <SpaceBetween size="s">
          <Link href="#/document-chat">Chat with LLM using your documents</Link>
          <Link href="#/ocr">Optical Character Recogintion (OCR)</Link>
        </SpaceBetween>
      </Box>
      <Box padding="s">
        <Box variant="h2">Demo</Box>
        <Box>
          <iframe
            width="100%"
            height="400"
            src="https://www.youtube.com/embed/your_video_id"
            title="Demo Video"
            frameBorder="0"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          ></iframe>
        </Box>
      </Box>
    </Grid>
  </ContentLayout>
);
}
