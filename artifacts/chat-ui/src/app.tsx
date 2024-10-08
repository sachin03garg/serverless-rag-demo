import { useState, useEffect } from "react";
import { Routes, Route, HashRouter } from 'react-router-dom';
import { AppLayout, TopNavigation, SideNavigation, Alert } from '@cloudscape-design/components';
import { Hub } from 'aws-amplify/utils';
import { signOut } from 'aws-amplify/auth';
import { AppContext } from "./common/context";
import { NotFound, ChatPage, HomePage, Help} from './pages'
import '@aws-amplify/ui-react/styles.css';

export default function App() {
  const [activeHref, setActiveHref] = useState("#/");
  const [utility, setUtility] = useState([])
  const [appData, setAppData] = useState({ userinfo: null })
  const [notificationVisible, setNotificationVisible] = useState(false);
  const [notificationMsg, setNotificationMsg] = useState("");
  const Router = HashRouter;

  useEffect(() => {
    Hub.listen("auth", (data) => {
      // setNotificationVisible(true);
      // setNotificationMsg("Validating Authentication")
      switch (data.payload.event) {
        case "signedOut":
          setAppData({ userinfo: null })
          break;
      }
    })
  }, [])

  useEffect(() => {
    if (appData.userinfo != null) {
      setUtility([{
        type: "menu-dropdown",
        text: "Logout",
        description: appData.userinfo.signInDetails.loginId,
        iconName: "user-profile",
        onItemClick: (e) => {
          if (e.detail.id == 'signout') { signOut({ global: true }) }
        },
        items: [
          { id: "signout", text: "Sign out" }
        ]
      }])
    } else {
      setUtility([])
    }
  }, [appData])


  return (
    <AppContext.Provider value={appData}>
      <div id="custom-main-header" style={{ position: 'sticky', top: 0, zIndex: 1002 }}><TopNavigation
        identity={{
          href: '#',
          title: 'IPS Contract Orchestrator (Development)',
        }}

        utilities={[
          ...utility
        ]}

      /></div>
      <AppLayout
        disableContentPaddings
        headerSelector='#custom-main-header'
        toolsHide={false}
        tools={
          <Router>
            <Routes>
              <Route path="/" element={<Help setPageId="home" />} />
              <Route path="/document-chat" element={<Help setPageId="doc-chat" />} />
              <Route path="/document-chat/manage-document" element={<Help setPageId="doc-chat-manage" />} />
              <Route path="*" element={<Help setPageId="404" />} />
            </Routes>
          </Router>
        }
        notifications={(notificationVisible) ? <Alert dismissible statusIconAriaLabel="Warning" type="warning" onDismiss={() => setNotificationVisible(false)}>{notificationMsg}</Alert> : ""}
        navigation={<SideNavigation
          activeHref={activeHref}
          header={{ href: "#/", text: "Accelerators" }}
          onFollow={event => {
            if (!event.detail.external) {
              setActiveHref(event.detail.href);
            }
          }}
          items={[
            {
              type: "link-group", text: "Chat with LLM", href: "#/document-chat",
              items: [
                { type: "link", text: "Manage Documents", href: "#/document-chat/manage-document" },
              ]
            },
          ]}
        />}
        content={
          <Router>
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/document-chat" element={<ChatPage setAppData={setAppData} manageDocument={false} />} />
              <Route path="/document-chat/manage-document" element={<ChatPage setAppData={setAppData} manageDocument={true} />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Router>
        }
      />
    </AppContext.Provider>
  );
}
