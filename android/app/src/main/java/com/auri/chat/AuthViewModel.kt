package com.auri.chat

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class AuthViewModel(application: Application) : AndroidViewModel(application) {
    private val api = AuthApi()
    private val authStore = AuthStore(application)

    var email by mutableStateOf("")
    var password by mutableStateOf("")
    var verificationCode by mutableStateOf("")
    var isRegisterMode by mutableStateOf(false)
    var isSubmitting by mutableStateOf(false)
    var isSendingCode by mutableStateOf(false)
    var resendSeconds by mutableIntStateOf(0)
    var error by mutableStateOf<String?>(null)
    var notice by mutableStateOf<String?>(null)
    var consentAgreed by mutableStateOf(PrivacyConsent.isAgreed(application))
        private set
    private var countdownJob: Job? = null

    fun setConsent(agreed: Boolean) {
        consentAgreed = agreed
        PrivacyConsent.setAgreed(getApplication(), agreed)
    }

    fun switchMode(register: Boolean) {
        isRegisterMode = register
        error = null
        notice = null
        verificationCode = ""
        countdownJob?.cancel()
        resendSeconds = 0
    }

    fun requestVerificationCode() {
        if (!consentAgreed) {
            error = "请先阅读并同意隐私政策"
            return
        }
        val normalizedEmail = email.trim()
        if (!isValidEmail(normalizedEmail)) {
            error = "请输入有效邮箱"
            return
        }
        if (isSendingCode || resendSeconds > 0) return

        isSendingCode = true
        error = null
        notice = null
        viewModelScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.requestRegistrationCode(normalizedEmail)
                }
                notice = "验证码已发送，请检查收件箱和垃圾邮件"
                startCountdown(result.resendAfterSeconds)
            } catch (exception: Exception) {
                error = exception.message ?: "验证码发送失败"
            } finally {
                isSendingCode = false
            }
        }
    }

    fun submit(onSuccess: () -> Unit) {
        if (!consentAgreed) {
            error = "请先阅读并同意隐私政策"
            return
        }
        val normalizedEmail = email.trim()
        val normalizedPassword = password
        if (!isValidEmail(normalizedEmail)) {
            error = "请输入有效邮箱"
            return
        }
        if (normalizedPassword.length < 6) {
            error = "密码至少 6 位"
            return
        }
        val normalizedCode = verificationCode.trim()
        if (isRegisterMode && !normalizedCode.matches(Regex("^\\d{6}$"))) {
            error = "请输入 6 位邮箱验证码"
            return
        }

        isSubmitting = true
        error = null
        viewModelScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    if (isRegisterMode) {
                        api.register(normalizedEmail, normalizedPassword, normalizedCode)
                    } else {
                        api.login(normalizedEmail, normalizedPassword)
                    }
                }
                authStore.save(result.token, result.email)
                onSuccess()
            } catch (exception: Exception) {
                error = exception.message ?: "操作失败"
            } finally {
                isSubmitting = false
            }
        }
    }

    private fun startCountdown(seconds: Int) {
        countdownJob?.cancel()
        countdownJob = viewModelScope.launch {
            resendSeconds = seconds.coerceAtLeast(1)
            while (resendSeconds > 0) {
                delay(1_000)
                resendSeconds -= 1
            }
        }
    }

    private fun isValidEmail(value: String): Boolean {
        val at = value.indexOf('@')
        val dot = value.lastIndexOf('.')
        return at in 1 until value.lastIndex && dot > at + 1 && dot < value.lastIndex
    }
}
