import { useRef, useState } from "react";
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View, useColorScheme } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { WebView } from "react-native-webview";

import { APP_NAME, APP_URL } from "./config";

const HOST = new URL(APP_URL).host;

export default function App() {
  const dark = useColorScheme() === "dark";
  const web = useRef(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const colors = dark ? { bg: "#0e1117", text: "#fafafa" } : { bg: "#ffffff", text: "#1f2328" };

  const retry = () => {
    setFailed(false);
    setLoading(true);
    web.current?.reload();
  };

  // Links to other sites (stores, TMDB...) open in Safari; the app itself stays in the WebView.
  const allow = (request) => {
    let host = "";
    try {
      host = new URL(request.url).host;
    } catch {
      return true;
    }
    if (host === HOST || host.endsWith(".streamlit.app") || request.url === "about:blank") return true;
    if (request.navigationType === "click" || request.isTopFrame) {
      Linking.openURL(request.url);
      return false;
    }
    return true;
  };

  return (
    <SafeAreaProvider>
      <SafeAreaView style={[styles.fill, { backgroundColor: colors.bg }]}>
        <StatusBar style={dark ? "light" : "dark"} />
        {failed ? (
          <View style={styles.center}>
            <Text style={[styles.title, { color: colors.text }]}>Can't reach {APP_NAME}</Text>
            <Text style={[styles.hint, { color: colors.text }]}>
              Check your internet connection. If the app was idle it may still be waking up.
            </Text>
            <Pressable style={styles.button} onPress={retry}>
              <Text style={styles.buttonText}>Try again</Text>
            </Pressable>
          </View>
        ) : (
          <WebView
            ref={web}
            source={{ uri: APP_URL }}
            style={[styles.fill, { backgroundColor: colors.bg }]}
            onLoadEnd={() => setLoading(false)}
            onError={() => setFailed(true)}
            onHttpError={(e) => e.nativeEvent.statusCode >= 500 && setFailed(true)}
            onShouldStartLoadWithRequest={allow}
            pullToRefreshEnabled
            allowsBackForwardNavigationGestures
            sharedCookiesEnabled
            allowsInlineMediaPlayback
            setSupportMultipleWindows={false}
          />
        )}
        {loading && !failed && (
          <View style={[styles.overlay, { backgroundColor: colors.bg }]} pointerEvents="none">
            <ActivityIndicator size="large" />
            <Text style={[styles.hint, { color: colors.text }]}>Opening {APP_NAME}…</Text>
          </View>
        )}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  overlay: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", gap: 12 },
  title: { fontSize: 20, fontWeight: "600", marginBottom: 8 },
  hint: { fontSize: 15, textAlign: "center", opacity: 0.7 },
  button: { marginTop: 20, backgroundColor: "#ff4b4b", paddingHorizontal: 24, paddingVertical: 12, borderRadius: 10 },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
});
