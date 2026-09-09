package com.auri.chat

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import org.json.JSONObject

@Composable
fun PrivacyPolicyDialog(onDismiss: () -> Unit, onAgree: (() -> Unit)? = null) {
    val context = LocalContext.current
    val uriHandler = LocalUriHandler.current
    val document = remember {
        JSONObject(context.assets.open("privacy-policy.json").bufferedReader().use { it.readText() })
    }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(
        usePlatformDefaultWidth = false,
        decorFitsSystemWindows = false,
    )) {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(modifier = Modifier.fillMaxSize().safeDrawingPadding()) {
                Text("隐私政策", style = MaterialTheme.typography.headlineSmall,
                    modifier = Modifier.padding(24.dp))
                Column(modifier = Modifier.weight(1f).verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("版本 ${document.getString("version")} · 生效日期 ${document.getString("effective_date")}",
                        style = MaterialTheme.typography.bodySmall)
                    Text(document.getString("introduction"))
                    val sections = document.getJSONArray("sections")
                    for (index in 0 until sections.length()) {
                        val section = sections.getJSONObject(index)
                        Text(section.getString("title"), style = MaterialTheme.typography.titleMedium)
                        Text(section.getString("body"), style = MaterialTheme.typography.bodyMedium)
                        section.optJSONArray("links")?.let { links ->
                            for (linkIndex in 0 until links.length()) {
                                val link = links.getJSONObject(linkIndex)
                                TextButton(onClick = { uriHandler.openUri(link.getString("url")) }) {
                                    Text(link.getString("label"))
                                }
                            }
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                }
                // Some full-screen dialog hosts consume navigation insets before Compose sees them.
                Row(modifier = Modifier.fillMaxWidth()
                    .padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 40.dp),
                    horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) { Text(if (onAgree == null) "关闭" else "暂不同意并退出") }
                    if (onAgree != null) Button(onClick = onAgree) { Text("同意并继续") }
                }
            }
        }
    }
}
