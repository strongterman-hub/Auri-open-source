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
import androidx.compose.material3.AlertDialog
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

private val VerificationButtonHeight = 56.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChangePasswordScreen(
    email: String,
    token: String,
    onBack: () -> Unit,
    onPasswordChanged: (AuthResult) -> Unit,
) {
    val api = remember { AuthApi() }
    val scope = rememberCoroutineScope()
    var verificationCode by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }
    var sendingCode by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var resendSeconds by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var changed by remember { mutableStateOf(false) }

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
                                imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    title = { Text("修改密码") },
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
                    .imePadding()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp, vertical = 28.dp),
            ) {
                Text(
                    text = "验证当前邮箱",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onBackground,
                )
                Text(
                    text = "验证码将发送至 ${email.ifBlank { "当前账号邮箱" }}。修改成功后，其他设备需要使用新密码重新登录。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )

                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 24.dp),
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
                        shape = RoundedCornerShape(14.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        colors = passwordFieldColors(),
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedButton(
                        onClick = {
                            if (sendingCode || resendSeconds > 0) return@OutlinedButton
                            sendingCode = true
                            error = null
                            notice = null
                            scope.launch {
                                try {
                                    val result = withContext(Dispatchers.IO) {
                                        api.requestPasswordChangeCode(token)
                                    }
                                    resendSeconds = result.resendAfterSeconds.coerceAtLeast(1)
                                    notice = "验证码已发送，请检查收件箱和垃圾邮件"
                                } catch (exception: Exception) {
                                    error = exception.message ?: "验证码发送失败"
                                } finally {
                                    sendingCode = false
                                }
                            }
                        },
                        enabled = !sendingCode && resendSeconds == 0 && !submitting,
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier
                            .padding(start = 10.dp)
                            .height(VerificationButtonHeight),
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
                    onValueChange = {
                        newPassword = it
                        error = null
                    },
                    label = { Text("新密码（至少 6 位）") },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    colors = passwordFieldColors(),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 16.dp),
                )
                OutlinedTextField(
                    value = confirmPassword,
                    onValueChange = {
                        confirmPassword = it
                        error = null
                    },
                    label = { Text("再次输入新密码") },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp),
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    colors = passwordFieldColors(),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                )

                notice?.let {
                    Text(
                        text = it,
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
                error?.let {
                    Text(
                        text = it,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }

                Spacer(modifier = Modifier.height(24.dp))
                Button(
                    onClick = {
                        val validationError = validatePasswordChange(
                            verificationCode = verificationCode,
                            newPassword = newPassword,
                            confirmPassword = confirmPassword,
                        )
                        if (validationError != null) {
                            error = validationError
                        } else {
                                submitting = true
                                error = null
                                scope.launch {
                                    try {
                                        val result = withContext(Dispatchers.IO) {
                                            api.changePassword(
                                                token = token,
                                                verificationCode = verificationCode,
                                                newPassword = newPassword,
                                            )
                                        }
                                        onPasswordChanged(result)
                                        changed = true
                                    } catch (exception: Exception) {
                                        error = exception.message ?: "密码修改失败"
                                    } finally {
                                        submitting = false
                                    }
                                }
                        }
                    },
                    enabled = !submitting,
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(52.dp),
                ) {
                    if (submitting) {
                        CircularProgressIndicator(
                            modifier = Modifier.padding(end = 8.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    }
                    Text("确认修改")
                }
            }
        }
    }

    if (changed) {
        AlertDialog(
            onDismissRequest = {},
            title = { Text("密码已修改") },
            text = { Text("本设备可以继续使用，其他设备需要使用新密码重新登录。") },
            confirmButton = {
                TextButton(onClick = onBack) {
                    Text("完成")
                }
            },
        )
    }
}

@Composable
private fun passwordFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = MaterialTheme.colorScheme.primary,
    unfocusedBorderColor = MaterialTheme.colorScheme.outline,
)

internal fun validatePasswordChange(
    verificationCode: String,
    newPassword: String,
    confirmPassword: String,
): String? = when {
    !verificationCode.matches(Regex("^\\d{6}$")) -> "请输入 6 位邮箱验证码"
    newPassword.length < 6 -> "新密码至少 6 位"
    newPassword != confirmPassword -> "两次输入的密码不一致"
    else -> null
}
