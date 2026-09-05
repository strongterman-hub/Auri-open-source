package com.auri.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class XiaomiConnectPhase {
    Idle,
    LoadingQr,
    WaitingScan,
    Syncing,
    Connected,
    Failed,
}

data class XiaomiConnectUiState(
    val phase: XiaomiConnectPhase = XiaomiConnectPhase.Idle,
    val qrImagePngBase64: String? = null,
    val error: String? = null,
)

class XiaomiConnectViewModel(application: Application) : AndroidViewModel(application) {
    private val authStore = AuthStore(application)
    private val api = XiaomiApi()
    private val _state = MutableStateFlow(XiaomiConnectUiState())
    val state: StateFlow<XiaomiConnectUiState> = _state.asStateFlow()

    private var pollJob: Job? = null

    fun startLogin() {
        val token = authStore.getToken()
        if (token == null) {
            _state.value = XiaomiConnectUiState(phase = XiaomiConnectPhase.Failed, error = "未登录")
            return
        }
        _state.value = XiaomiConnectUiState(phase = XiaomiConnectPhase.LoadingQr)
        viewModelScope.launch {
            try {
                val start = retry(times = 3) {
                    withContext(Dispatchers.IO) { api.startLogin(token) }
                }
                _state.value = XiaomiConnectUiState(
                    phase = XiaomiConnectPhase.WaitingScan,
                    qrImagePngBase64 = start.qrImagePngBase64,
                )
                startPolling(token, start.sessionId)
            } catch (e: Exception) {
                _state.value = XiaomiConnectUiState(
                    phase = XiaomiConnectPhase.Failed,
                    error = e.message ?: "获取二维码失败",
                )
            }
        }
    }

    private fun startPolling(token: String, sessionId: String) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            var consecutiveFailures = 0
            while (isActive) {
                delay(2_000)
                val result = try {
                    withContext(Dispatchers.IO) { api.pollLoginStatus(token, sessionId) }
                } catch (e: Exception) {
                    consecutiveFailures++
                    if (consecutiveFailures >= 15) {
                        _state.value = XiaomiConnectUiState(
                            phase = XiaomiConnectPhase.Failed,
                            error = e.message ?: "查询登录状态失败",
                        )
                        return@launch
                    }
                    continue
                }
                consecutiveFailures = 0
                when (result.status) {
                    "connected" -> {
                        syncAfterConnect(token)
                        return@launch
                    }

                    "expired" -> {
                        _state.value = XiaomiConnectUiState(
                            phase = XiaomiConnectPhase.Failed,
                            error = "二维码已过期，请重新获取",
                        )
                        return@launch
                    }

                    "failed" -> {
                        _state.value = XiaomiConnectUiState(
                            phase = XiaomiConnectPhase.Failed,
                            error = result.error ?: "登录失败",
                        )
                        return@launch
                    }
                }
            }
        }
    }

    private fun syncAfterConnect(token: String) {
        _state.value = _state.value.copy(
            phase = XiaomiConnectPhase.Syncing,
            qrImagePngBase64 = null,
        )
        viewModelScope.launch {
            try {
                retry(times = 5) {
                    withContext(Dispatchers.IO) { api.triggerSync(token) }
                }
                _state.value = XiaomiConnectUiState(phase = XiaomiConnectPhase.Connected)
            } catch (e: Exception) {
                _state.value = XiaomiConnectUiState(
                    phase = XiaomiConnectPhase.Failed,
                    error = e.message ?: "同步小米健康数据失败",
                )
            }
        }
    }

    private suspend fun <T> retry(times: Int, block: suspend () -> T): T {
        var lastError: Throwable? = null
        repeat(times) { attempt ->
            try {
                return block()
            } catch (e: Exception) {
                lastError = e
                if (attempt < times - 1) delay(2_000)
            }
        }
        throw lastError ?: IllegalStateException("重试失败")
    }

    fun reset() {
        pollJob?.cancel()
        pollJob = null
        _state.value = XiaomiConnectUiState(phase = XiaomiConnectPhase.Idle)
    }

    override fun onCleared() {
        pollJob?.cancel()
        super.onCleared()
    }
}
