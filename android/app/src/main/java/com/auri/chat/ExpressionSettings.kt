package com.auri.chat

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
internal fun ExpressionSettingsGroup(model: ChatViewModel) {
    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
        Text("聊天表达", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurface)
        if (!model.expressionSettingsLoaded) {
            Text(model.expressionSettingsError ?: "正在加载…", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (model.expressionSettingsError != null) {
                TextButton(onClick = model::loadExpressionPreferences) { Text("重试") }
            }
        } else {
            ExpressionSwitch("连续消息", "让 Auri 自然地分几条说", model.continuousMessages,
                !model.expressionSettingsSaving) { model.updateExpressionPreferences(continuous = it) }
            ExpressionSwitch("表情包", "偶尔用表情回应你", model.stickersEnabled,
                !model.expressionSettingsSaving) { model.updateExpressionPreferences(stickers = it) }
            model.expressionSettingsError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }
}

@Composable
private fun ExpressionSwitch(title: String, subtitle: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}
