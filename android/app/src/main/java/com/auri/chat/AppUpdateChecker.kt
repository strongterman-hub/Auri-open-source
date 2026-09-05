package com.auri.chat

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class AppVersionState(
    val checking: Boolean = false,
    val available: UpdateInfo? = null,
    val checked: Boolean = false,
    val failed: Boolean = false,
)

/** Keeps version discovery independent of a dismissed update dialog. Call on the main thread. */
class AppUpdateChecker(
    private val currentVersionCode: Int,
    private val scope: CoroutineScope,
    private val fetch: suspend () -> UpdateInfo,
    private val onResult: (info: UpdateInfo?, manual: Boolean, prompt: Boolean) -> Unit,
) {
    private val mutableState = MutableStateFlow(AppVersionState())
    val state = mutableState.asStateFlow()
    private var manualRequested = false
    private var promptRequested = false

    fun check(manual: Boolean = false, prompt: Boolean = true) {
        manualRequested = manualRequested || manual
        promptRequested = promptRequested || prompt
        if (mutableState.value.checking) return
        mutableState.value = mutableState.value.copy(checking = true, failed = false)
        scope.launch {
            val info = try {
                fetch()
            } catch (cancelled: CancellationException) {
                mutableState.value = mutableState.value.copy(checking = false)
                manualRequested = false
                promptRequested = false
                throw cancelled
            } catch (_: Exception) {
                null
            }
            mutableState.value = if (info == null) {
                // A transient failure must not erase an already discovered update.
                mutableState.value.copy(checking = false, failed = true)
            } else {
                AppVersionState(
                    available = info.takeIf { it.versionCode > currentVersionCode },
                    checked = true,
                )
            }
            val manualResult = manualRequested
            val promptResult = promptRequested
            manualRequested = false
            promptRequested = false
            onResult(info, manualResult, promptResult)
        }
    }
}
