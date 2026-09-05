package com.auri.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ResetPasswordScreen(
    initialEmail: String,
    onBack: () -> Unit,
    onPasswordReset: (AuthResult) -> Unit,
) {
    val api = remember { AuthApi() }
    val scope = rememberCoroutineScope()
    var email by remember(initialEmail) { mutableStateOf(initialEmail.trim()) }
    var verificationCode by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }
    var sendingCode by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var resendSeconds by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(resendSeconds) {
        if (resendSeconds <= 0) return@LaunchedEffect
        delay(1_000)
        resendSeconds -= 1
    }
    BackHandler(enabled = !submitting, onBack = onBack)

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = {
                TopAppBar(
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !submitting) {
                            Icon(
                                Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                            )
                        }
                    },
                    title = { Text("找回密码") },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                    ),
                )
            },
        ) { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .imePadding()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp, vertical = 28.dp),
            ) {
                Text(
                    "通过注册邮箱验证身份。为保护账号，无论邮箱是否存在，获取验证码时都会显示相同结果。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = email,
                    onValueChange = { email = it.trim(); error = null },
                    label = { Text("注册邮箱") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                    shape = RoundedCornerShape(14.dp),
                    colors = resetFieldColors(),
                    modifier = Modifier.fillMaxWidth().padding(top = 20.dp),
                )
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                    verticalAlignment = Alignment.Bottom,
                ) {
                    OutlinedTextField(
                        value = verificationCode,
                        onValueChange = {
                            verificationCode = it.filter(Char::isDigit).take(6)
                            error = null
                        },
                        label = { Text("邮箱验证码") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        shape = RoundedCornerShape(14.dp),
                        colors = resetFieldColors(),
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedButton(
                        onClick = {
                            if (!emailLooksValid(email)) {
                                error = "请输入有效邮箱"
                                return@OutlinedButton
                            }
                            sendingCode = true
                            error = null
                            notice = null
                            scope.launch {
                                try {
                                    val result = withContext(Dispatchers.IO) {
                                        api.requestPasswordResetCode(email)
                                    }
                                    resendSeconds = result.resendAfterSeconds.coerceAtLeast(1)
                                    notice = "如该邮箱已注册，验证码会发送至收件箱"
                                } catch (exception: Exception) {
                                    error = exception.message ?: "验证码发送失败"
                                } finally {
                                    sendingCode = false
                                }
                            }
                        },
                        enabled = !sendingCode && resendSeconds == 0 && !submitting,
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.padding(start = 10.dp).height(56.dp),
                    ) {
                        Text(
                            when {
                                sendingCode -> "发送中"
                                resendSeconds > 0 -> "${resendSeconds}s"
                                else -> "获取验证码"
                            },
                        )
                    }
                }
                OutlinedTextField(
                    value = newPassword,
                    onValueChange = { newPassword = it; error = null },
                    label = { Text("新密码（至少 6 位）") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    shape = RoundedCornerShape(14.dp),
                    colors = resetFieldColors(),
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                )
                OutlinedTextField(
                    value = confirmPassword,
                    onValueChange = { confirmPassword = it; error = null },
                    label = { Text("再次输入新密码") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    shape = RoundedCornerShape(14.dp),
                    colors = resetFieldColors(),
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                )
                notice?.let {
                    Text(
                        it,
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
                error?.let {
                    Text(
                        it,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
                Spacer(Modifier.height(24.dp))
                Button(
                    onClick = {
                        error = validatePasswordReset(
                            email = email,
                            verificationCode = verificationCode,
                            newPassword = newPassword,
                            confirmPassword = confirmPassword,
                        )
                        if (error != null) return@Button
                        submitting = true
                        scope.launch {
                            try {
                                val result = withContext(Dispatchers.IO) {
                                    api.resetPassword(email, verificationCode, newPassword)
                                }
                                onPasswordReset(result)
                            } catch (exception: Exception) {
                                error = exception.message ?: "密码重置失败"
                            } finally {
                                submitting = false
                            }
                        }
                    },
                    enabled = !submitting,
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                ) {
                    if (submitting) {
                        CircularProgressIndicator(
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                            modifier = Modifier.padding(end = 8.dp),
                        )
                    }
                    Text("重置并登录")
                }
            }
        }
    }
}

@Composable
private fun resetFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = MaterialTheme.colorScheme.primary,
    unfocusedBorderColor = MaterialTheme.colorScheme.outline,
)

private fun emailLooksValid(value: String): Boolean {
    val at = value.indexOf('@')
    val dot = value.lastIndexOf('.')
    return at in 1 until value.lastIndex && dot > at + 1 && dot < value.lastIndex
}

internal fun validatePasswordReset(
    email: String,
    verificationCode: String,
    newPassword: String,
    confirmPassword: String,
): String? = when {
    !emailLooksValid(email) -> "请输入有效邮箱"
    !verificationCode.matches(Regex("^\\d{6}$")) -> "请输入 6 位邮箱验证码"
    newPassword.length < 6 -> "新密码至少 6 位"
    newPassword != confirmPassword -> "两次输入的密码不一致"
    else -> null
}
