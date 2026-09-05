package com.auri.chat

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.util.Log
import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.alipay.sdk.app.EnvUtils
import com.alipay.sdk.app.PayTask
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.NumberFormat


private const val CREDITS_PER_YUAN = 95
private const val MAX_RECHARGE_YUAN = 1000


@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreditsScreen(
    token: String,
    onBack: () -> Unit,
    onCheckout: (Int) -> Unit,
) {
    var amountText by remember { mutableStateOf("100") }
    var balance by remember { mutableStateOf<String?>(null) }
    val amount = amountText.toIntOrNull() ?: 0
    val validAmount = amount in 1..MAX_RECHARGE_YUAN
    val credits = amount * CREDITS_PER_YUAN
    val creditsText = NumberFormat.getIntegerInstance().format(credits)

    LaunchedEffect(token) {
        balance = withContext(Dispatchers.IO) {
            runCatching { CreditsApi().balance(token).balanceCredits }.getOrNull()
        }
    }
    BackHandler(onBack = onBack)

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = { CreditsTopBar("Credits", onBack) },
            bottomBar = {
                Surface(color = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f)) {
                    Button(
                        onClick = { onCheckout(amount) },
                        enabled = validAmount,
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
                balance?.let {
                    Text(
                        "当前余额  ${formatCredits(it)} Credits",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(20.dp))
                }
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
                        amountText = value.filter(Char::isDigit).take(4)
                    },
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Text("¥", style = MaterialTheme.typography.titleLarge) },
                    suffix = { Text("元") },
                    supportingText = if (amount > MAX_RECHARGE_YUAN) {
                        { Text("单次充值金额为 ¥1–¥$MAX_RECHARGE_YUAN") }
                    } else null,
                    isError = amount > MAX_RECHARGE_YUAN,
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


private sealed interface PaymentState {
    data object Idle : PaymentState
    data object Paying : PaymentState
    data object Checking : PaymentState
    data object Paid : PaymentState
    data class Error(val message: String) : PaymentState
}


@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreditsCheckoutScreen(
    token: String,
    amountYuan: Int,
    onBack: () -> Unit,
    onPaid: () -> Unit,
) {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    val scope = rememberCoroutineScope()
    val credits = amountYuan * CREDITS_PER_YUAN
    val creditsText = NumberFormat.getIntegerInstance().format(credits)
    var state by remember { mutableStateOf<PaymentState>(PaymentState.Idle) }
    BackHandler(enabled = state !is PaymentState.Paying, onBack = onBack)

    fun startPayment() {
        if (activity == null || state is PaymentState.Paying || state is PaymentState.Checking) return
        scope.launch {
            state = PaymentState.Paying
            val outcome = runCatching {
                val api = CreditsApi()
                val order = withContext(Dispatchers.IO) { api.createOrder(amountYuan, token) }
                val orderString = requireNotNull(order.orderString)
                val paymentResult = withContext(Dispatchers.IO) {
                    EnvUtils.setEnv(
                        if (BuildConfig.ALIPAY_SANDBOX) {
                            EnvUtils.EnvEnum.SANDBOX
                        } else {
                            EnvUtils.EnvEnum.ONLINE
                        },
                    )
                    PayTask(activity).payV2(orderString, true)
                }
                if (BuildConfig.DEBUG) {
                    Log.d(
                        "AuriAlipay",
                        "resultStatus=${paymentResult["resultStatus"]}, " +
                            "memo=${paymentResult["memo"]}, result=${paymentResult["result"]}",
                    )
                }
                when (paymentResult["resultStatus"]) {
                    "6001" -> {
                        state = PaymentState.Error("已取消支付")
                        return@launch
                    }
                    "6002" -> {
                        state = PaymentState.Error("网络连接异常，请稍后重试")
                        return@launch
                    }
                    "4000" -> {
                        state = PaymentState.Error("支付未完成，请稍后重试")
                        return@launch
                    }
                }
                state = PaymentState.Checking
                var latest = order
                repeat(5) { attempt ->
                    latest = withContext(Dispatchers.IO) {
                        api.order(order.orderId, token, refresh = attempt == 0 || attempt == 4)
                    }
                    if (latest.status == "paid") return@runCatching true
                    if (latest.status == "closed") return@runCatching false
                    delay(1_500)
                }
                false
            }
            state = outcome.fold(
                onSuccess = { paid ->
                    if (paid) PaymentState.Paid else PaymentState.Error("暂未确认到账，请稍后重试")
                },
                onFailure = { PaymentState.Error(it.message ?: "支付未完成") },
            )
        }
    }

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = { CreditsTopBar("确认支付", onBack) },
            bottomBar = {
                Surface(color = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f)) {
                    Button(
                        onClick = if (state is PaymentState.Paid) onPaid else ::startPayment,
                        enabled = state !is PaymentState.Paying && state !is PaymentState.Checking,
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(horizontal = 16.dp, vertical = 14.dp)
                            .height(50.dp),
                        shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = AuriTokens.Primary,
                            contentColor = Color.White,
                        ),
                    ) {
                        when (state) {
                            PaymentState.Paying, PaymentState.Checking -> {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(20.dp),
                                    strokeWidth = 2.dp,
                                    color = Color.White,
                                )
                                Spacer(Modifier.width(10.dp))
                                Text(if (state is PaymentState.Paying) "正在打开支付宝" else "正在确认到账")
                            }
                            PaymentState.Paid -> Text("支付成功 · 完成")
                            else -> Text("支付宝支付 · ¥${amountYuan}.00")
                        }
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
                            "$creditsText Credits",
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
                                "¥${amountYuan}.00",
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
                        RadioButton(selected = true, onClick = null)
                    }
                }
                when (val current = state) {
                    PaymentState.Paid -> {
                        Spacer(Modifier.height(24.dp))
                        Text("充值已到账", color = AuriTokens.Secondary)
                    }
                    is PaymentState.Error -> {
                        Spacer(Modifier.height(24.dp))
                        Text(current.message, color = MaterialTheme.colorScheme.error)
                    }
                    else -> Unit
                }
            }
        }
    }
}


@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CreditsTopBar(title: String, onBack: () -> Unit) {
    TopAppBar(
        navigationIcon = {
            TextButton(onClick = onBack) {
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


private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}


internal fun formatCredits(value: String): String {
    val number = value.toDoubleOrNull() ?: return value
    return if (number % 1.0 == 0.0) {
        NumberFormat.getIntegerInstance().format(number.toLong())
    } else {
        String.format("%,.2f", number)
    }
}
