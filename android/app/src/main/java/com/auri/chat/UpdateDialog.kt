package com.auri.chat

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun UpdateDialog(
    state: UpdateUiState,
    onDownload: (UpdateInfo) -> Unit,
    onInstall: (UpdateUiState.Ready) -> Unit,
    onDismiss: () -> Unit,
    onRetry: () -> Unit,
) {
    when (state) {
        is UpdateUiState.Available -> {
            val info = state.info
            AlertDialog(
                onDismissRequest = { if (!info.force) onDismiss() },
                title = { Text("发现新版本 ${info.versionName}") },
                text = {
                    Column(
                        modifier = Modifier
                            .heightIn(max = 260.dp)
                            .verticalScroll(rememberScrollState()),
                    ) {
                        Text(info.changelog.ifBlank { "本次更新包含功能优化与问题修复。" })
                        if (info.apkSize > 0) {
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = "安装包大小：${formatBytes(info.apkSize)}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                confirmButton = {
                    Button(onClick = { onDownload(info) }) {
                        Text("立即更新")
                    }
                },
                dismissButton = {
                    if (!info.force) {
                        TextButton(onClick = onDismiss) {
                            Text("暂不更新")
                        }
                    }
                },
            )
        }

        is UpdateUiState.Downloading -> {
            val progress =
                if (state.total > 0) (state.received.toFloat() / state.total.toFloat()) else null
            AlertDialog(
                onDismissRequest = {},
                title = { Text("正在下载更新") },
                text = {
                    Column {
                        if (progress != null) {
                            LinearProgressIndicator(
                                progress = { progress },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Text("${formatBytes(state.received)} / ${formatBytes(state.total)}")
                        } else {
                            CircularProgressIndicator()
                            Spacer(modifier = Modifier.height(8.dp))
                            Text("正在下载…")
                        }
                    }
                },
                confirmButton = {},
            )
        }

        is UpdateUiState.Ready -> {
            AlertDialog(
                onDismissRequest = {},
                title = { Text("下载完成") },
                text = { Text("已下载新版本 ${state.info.versionName}，点击安装完成更新。") },
                confirmButton = {
                    Button(onClick = { onInstall(state) }) {
                        Text("安装")
                    }
                },
                dismissButton = {},
            )
        }

        is UpdateUiState.Error -> {
            val canDismiss = state.info == null || !state.info.force
            AlertDialog(
                onDismissRequest = { if (canDismiss) onDismiss() else onRetry() },
                title = { Text("更新失败") },
                text = { Text(state.message) },
                confirmButton = {
                    Button(onClick = onRetry) {
                        Text("重试")
                    }
                },
                dismissButton = {
                    if (canDismiss) {
                        TextButton(onClick = onDismiss) {
                            Text("取消")
                        }
                    }
                },
            )
        }

        UpdateUiState.Hidden -> Unit
    }
}

private fun formatBytes(bytes: Long): String {
    if (bytes <= 0) return "0 B"
    val kb = bytes / 1024.0
    if (kb < 1024) return "%.1f KB".format(kb)
    val mb = kb / 1024.0
    return "%.1f MB".format(mb)
}
