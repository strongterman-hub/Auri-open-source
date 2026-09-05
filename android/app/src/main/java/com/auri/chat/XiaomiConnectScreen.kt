package com.auri.chat

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun XiaomiConnectScreen(
    onBack: () -> Unit,
    onConnected: () -> Unit,
    viewModel: XiaomiConnectViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsState()
    LaunchedEffect(Unit) { viewModel.reset() }

    BackHandler(onBack = onBack)

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = {
                TopAppBar(
                    title = { Text("连接小米健康") },
                    navigationIcon = {
                        TextButton(onClick = onBack) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                        titleContentColor = MaterialTheme.colorScheme.onSurface,
                    ),
                )
            },
        ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Spacer(modifier = Modifier.height(12.dp))
            when (state.phase) {
                XiaomiConnectPhase.Idle -> IdleContent(onStart = viewModel::startLogin)
                XiaomiConnectPhase.LoadingQr -> LoadingContent("正在生成二维码…")
                XiaomiConnectPhase.WaitingScan -> QrContent(
                    qrImagePngBase64 = state.qrImagePngBase64,
                )

                XiaomiConnectPhase.Syncing -> LoadingContent("登录成功，正在同步健康数据…")
                XiaomiConnectPhase.Connected -> ConnectedContent(onDone = onConnected)
                XiaomiConnectPhase.Failed -> FailedContent(
                    error = state.error,
                    onRetry = viewModel::startLogin,
                )
            }
        }
    }
    }
}

@Composable
private fun IdleContent(onStart: () -> Unit) {
    Text(
        "登录小米账号后，Auri 将直接从小米健康云拉取手环数据。",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodyMedium,
    )
    Button(onClick = onStart) {
        Text("开始扫码登录")
    }
}

@Composable
private fun LoadingContent(label: String) {
    CircularProgressIndicator(modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
    Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun QrContent(qrImagePngBase64: String?) {
    val qr = remember(qrImagePngBase64) { qrImagePngBase64?.let(::decodeQrPng) }
    Text(
        "请使用小米运动健康或米家 App 扫码登录",
        color = MaterialTheme.colorScheme.onSurface,
        style = MaterialTheme.typography.titleSmall,
    )
    Card(
        modifier = Modifier.size(260.dp),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            if (qr != null) {
                Image(
                    bitmap = qr,
                    contentDescription = "小米登录二维码",
                    modifier = Modifier.size(228.dp),
                )
            } else {
                Text("二维码加载失败", color = MaterialTheme.colorScheme.error)
            }
        }
    }
    Text(
        "扫码成功后会自动完成同步，请保持页面打开。",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall,
    )
}

@Composable
private fun ConnectedContent(onDone: () -> Unit) {
    Text(
        "已连接小米健康，健康数据已同步。",
        color = MaterialTheme.colorScheme.secondary,
        style = MaterialTheme.typography.titleMedium,
    )
    Button(onClick = onDone, modifier = Modifier.fillMaxWidth()) {
        Text("完成")
    }
}

@Composable
private fun FailedContent(error: String?, onRetry: () -> Unit) {
    Text(
        error ?: "连接失败，请重试。",
        color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodyMedium,
    )
    Button(onClick = onRetry, modifier = Modifier.fillMaxWidth()) {
        Text("重试")
    }
}

private fun decodeQrPng(base64: String): ImageBitmap? =
    runCatching {
        val bytes = Base64.decode(base64, Base64.DEFAULT)
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
    }.getOrNull()
