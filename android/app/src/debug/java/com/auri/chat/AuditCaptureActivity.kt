package com.auri.chat

import android.graphics.Color as AndroidColor
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking

/** Debug-only pages for Alipay's pre-launch application review. */
class AuditCaptureActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
        )
        val page = intent.getStringExtra("page") ?: "home"
        if (page == "home") seedAuditConversation()
        setContent {
            AuriTheme {
                when (page) {
                    "credits" -> CreditsServiceAuditScreen()
                    "checkout" -> CreditsCheckoutAuditScreen()
                    else -> ChatScreen(
                        onOpenHealth = {},
                        onOpenAccount = {},
                        onOpenSettings = {},
                        onOpenCredits = {},
                    )
                }
            }
        }
    }

    private fun seedAuditConversation() = runBlocking(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        AuriDatabase.get(applicationContext).chatMessageDao().apply {
            clearAll()
            upsertAll(
                listOf(
                    ChatMessageEntity(
                        id = "audit-1",
                        role = "assistant",
                        content = "你好，我是 Auri。今天有什么想和我聊聊的吗？",
                        isUser = false,
                        timestamp = now - 120_000,
                    ),
                    ChatMessageEntity(
                        id = "audit-2",
                        role = "user",
                        content = "帮我整理一下今天的计划。",
                        isUser = true,
                        timestamp = now - 60_000,
                    ),
                    ChatMessageEntity(
                        id = "audit-3",
                        role = "assistant",
                        content = "可以。告诉我今天要做的几件事，我会帮你排出优先级和时间。",
                        isUser = false,
                        timestamp = now,
                    ),
                ),
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AuditTopBar(title: String) {
    TopAppBar(
        navigationIcon = {
            TextButton(onClick = {}) {
                Icon(
                    imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                    contentDescription = "返回",
                    tint = MaterialTheme.colorScheme.onSurface,
                )
            }
        },
        title = { Text(title, color = MaterialTheme.colorScheme.onSurface) },
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
            titleContentColor = MaterialTheme.colorScheme.onSurface,
        ),
    )
}

@Composable
private fun CreditsServiceAuditScreen() {
    var amountText by remember { mutableStateOf("100") }
    val amount = amountText.toIntOrNull() ?: 0
    val credits = amount * 95
    val creditsText = String.format("%,d", credits)

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = { AuditTopBar("Credits") },
            bottomBar = {
                Surface(color = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f)) {
                    Button(
                        onClick = {},
                        enabled = amount > 0,
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 16.dp, vertical = 14.dp)
                            .height(50.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Text("购买 $creditsText Credits · ¥$amount")
                    }
                }
            },
        ) { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .verticalScroll(rememberScrollState())
                    .padding(20.dp),
            ) {
                Text(
                    "购买 Credits",
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "用于 Auri 内的 AI 对话、图片理解、记忆与主动消息服务",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(28.dp))
                Text(
                    "充值金额",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = amountText,
                    onValueChange = { value ->
                        amountText = value.filter { it.isDigit() }.take(5)
                    },
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Text("¥", style = MaterialTheme.typography.titleLarge) },
                    suffix = { Text("元") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    shape = RoundedCornerShape(14.dp),
                )
                Spacer(Modifier.height(14.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    listOf(10, 30, 50, 100).forEach { quickAmount ->
                        AmountChip(
                            amount = quickAmount,
                            selected = amount == quickAmount,
                            onClick = { amountText = quickAmount.toString() },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
                Spacer(Modifier.height(24.dp))
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(14.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Row(
                        modifier = Modifier.padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                "本次获得",
                                style = MaterialTheme.typography.titleSmall,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "$creditsText Credits",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.SemiBold,
                                color = AuriTokens.Primary,
                            )
                        }
                        Text(
                            "¥$amount",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AmountChip(
    amount: Int,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val shape = RoundedCornerShape(14.dp)
    Surface(
        modifier = modifier
            .height(48.dp)
            .border(
                width = if (selected) 1.5.dp else 1.dp,
                color = if (selected) AuriTokens.Primary else AuriTokens.Outline,
                shape = shape,
            )
            .clickable(onClick = onClick),
        shape = shape,
        color = if (selected) AuriTokens.Primary.copy(alpha = 0.12f)
            else MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(
                "¥$amount",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                color = if (selected) AuriTokens.Primary else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

@Composable
private fun CreditsCheckoutAuditScreen() {
    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = { AuditTopBar("确认支付") },
            bottomBar = {
                Surface(color = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f)) {
                    Button(
                        onClick = {},
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 16.dp, vertical = 14.dp)
                            .height(50.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Text("支付宝支付 · ¥100.00")
                    }
                }
            },
        ) { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .padding(20.dp),
            ) {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    border = BorderStroke(1.dp, AuriTokens.Outline),
                ) {
                    Column(Modifier.padding(18.dp)) {
                        Text(
                            "Auri AI 助手服务",
                            style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "9,500 Credits",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(20.dp))
                        Divider(color = AuriTokens.Outline)
                        Spacer(Modifier.height(18.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                "应付金额",
                                modifier = Modifier.weight(1f),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(
                                "¥100.00",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    }
                }
                Spacer(Modifier.height(28.dp))
                Text(
                    "支付方式",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(12.dp))
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(14.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    border = BorderStroke(1.dp, AuriTokens.Outline),
                ) {
                    Row(
                        modifier = Modifier.padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Surface(
                            modifier = Modifier.size(40.dp),
                            shape = RoundedCornerShape(10.dp),
                            color = Color(0xFF1677FF),
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text("支", color = Color.White, fontWeight = FontWeight.Bold)
                            }
                        }
                        Spacer(Modifier.width(12.dp))
                        Text(
                            "支付宝",
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        RadioButton(selected = true, onClick = {})
                    }
                }
                Spacer(Modifier.height(22.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(
                        modifier = Modifier.size(20.dp),
                        shape = CircleShape,
                        color = AuriTokens.Primary,
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Text("✓", color = Color.White, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    Spacer(Modifier.width(9.dp))
                    Text(
                        "已阅读并同意《Credits 服务协议》与《退款说明》",
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
