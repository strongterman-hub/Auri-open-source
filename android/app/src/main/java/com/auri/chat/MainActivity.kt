package com.auri.chat

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import android.graphics.Color
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private var handledIntent by mutableStateOf<Intent?>(null)
    private var updateUiState by mutableStateOf<UpdateUiState>(UpdateUiState.Hidden)
    private lateinit var updateManager: AppUpdateManager
    private lateinit var updateChecker: AppUpdateChecker

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handledIntent = intent
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
        )
        ensureKeepAlive()
        val authStore = AuthStore(applicationContext)
        val sessionStore = SessionStore(applicationContext)
        val deviceStore = DeviceStore(applicationContext)
        updateManager = AppUpdateManager(applicationContext)
        updateChecker = AppUpdateChecker(
            currentVersionCode = updateManager.currentVersionCode(),
            scope = lifecycleScope,
            fetch = { withContext(Dispatchers.IO) { updateManager.check() } },
            onResult = { info, manual, prompt ->
                if (info != null && info.versionCode > updateManager.currentVersionCode()) {
                    if ((prompt || info.force) && updateUiState == UpdateUiState.Hidden) {
                        updateUiState = UpdateUiState.Available(info)
                    }
                } else if (manual) {
                    Toast.makeText(
                        this,
                        if (info == null) "检查更新失败，请稍后重试" else "已是最新版本",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            },
        )
        setContent {
            AuriTheme {
                val versionState by updateChecker.state.collectAsState()
                var token by remember { mutableStateOf(authStore.getToken()) }
                var privacyAgreed by remember { mutableStateOf(PrivacyConsent.isAgreed(applicationContext)) }
                var loggingOut by remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()
                var routeName by rememberSaveable { mutableStateOf(AuriRoute.Chat.name) }
                var rechargeAmount by rememberSaveable { mutableStateOf(100) }
                val route = AuriRoute.valueOf(routeName)

                LaunchedEffect(handledIntent) {
                    if (handledIntent?.action == ACTION_OPEN_CHAT) {
                        routeName = AuriRoute.Chat.name
                    }
                }

                if (token != null && !privacyAgreed) {
                    PrivacyPolicyDialog(
                        onDismiss = { finish() },
                        onAgree = {
                            PrivacyConsent.agree(applicationContext)
                            privacyAgreed = true
                            ensureKeepAlive()
                        },
                    )
                } else if (token == null) {
                    AccountScreen(
                        onLoggedIn = {
                            token = authStore.getToken()
                            privacyAgreed = PrivacyConsent.isAgreed(applicationContext)
                            ensureKeepAlive()
                            routeName = AuriRoute.Chat.name
                        },
                    )
                } else {
                    AnimatedContent(
                        targetState = route,
                        transitionSpec = {
                            val forward = initialState == AuriRoute.Chat
                            if (forward) {
                                (fadeIn(tween(220)) +
                                    slideInHorizontally(tween(220), initialOffsetX = { it / 4 }))
                                    .togetherWith(
                                        fadeOut(tween(180)) +
                                            slideOutHorizontally(tween(180), targetOffsetX = { -it / 4 }),
                                    )
                            } else {
                                (fadeIn(tween(220)) +
                                    slideInHorizontally(tween(220), initialOffsetX = { -it / 4 }))
                                    .togetherWith(
                                        fadeOut(tween(180)) +
                                            slideOutHorizontally(tween(180), targetOffsetX = { it / 4 }),
                                    )
                            }
                        },
                        label = "main-route",
                    ) { currentRoute ->
                        when (currentRoute) {
                        AuriRoute.Schedule -> ScheduleScreen(
                            onBack = { routeName = AuriRoute.Chat.name },
                        )

                        AuriRoute.Health -> HealthScreen(
                            onBack = { routeName = AuriRoute.Chat.name },
                        )

                        AuriRoute.Account -> AccountCenterScreen(
                            loggingOut = loggingOut,
                            email = authStore.getEmail().orEmpty(),
                            versionState = versionState,
                            onRefreshVersion = { triggerUpdateCheck(prompt = false) },
                            onCheckUpdate = { triggerUpdateCheck(manual = true) },
                            onBack = { routeName = AuriRoute.Chat.name },
                            onOpenCredits = { routeName = AuriRoute.Credits.name },
                            onOpenChangePassword = {
                                routeName = AuriRoute.ChangePassword.name
                            },
                            onLogout = {
                                if (!loggingOut) {
                                    loggingOut = true
                                    scope.launch {
                                        try {
                                            val currentToken = token
                                            val currentEmail = authStore.getEmail().orEmpty()
                                            val registrationId = deviceStore.getRegistrationId()
                                            withContext(Dispatchers.IO) {
                                                if (!hasNetworkConnection(applicationContext)) throw OfflineException()
                                                if (currentToken != null && registrationId != null) {
                                                    runCatching {
                                                        AuriApi().unregisterDevice(registrationId, currentToken)
                                                    }
                                                }
                                                currentToken?.let { AuthApi().logout(it) }
                                                AuriDatabase.get(applicationContext).chatMessageDao().clearAll()
                                            }
                                            authStore.clear()
                                            AuriKeepAliveController.stop(applicationContext)
                                            sessionStore.clearSession(currentEmail)
                                            token = null
                                            routeName = AuriRoute.Chat.name
                                            viewModelStore.clear()
                                        } catch (exception: Exception) {
                                            Toast.makeText(
                                                applicationContext,
                                                readableNetworkError(exception, "退出失败，请稍后重试"),
                                                Toast.LENGTH_LONG,
                                            ).show()
                                        } finally {
                                            loggingOut = false
                                        }
                                    }
                                }
                            },
                            onDeleteAccount = {
                                scope.launch {
                                    val currentEmail = authStore.getEmail().orEmpty()
                                    withContext(Dispatchers.IO) {
                                        AuriDatabase.get(applicationContext).chatMessageDao().clearAll()
                                    }
                                    authStore.clear()
                                    AuriKeepAliveController.stop(applicationContext)
                                    sessionStore.clearSession(currentEmail)
                                    token = null
                                    routeName = AuriRoute.Chat.name
                                    viewModelStore.clear()
                                }
                            },
                        )

                        AuriRoute.ChatSettings -> ChatSettingsScreen(
                            onBack = { routeName = AuriRoute.Chat.name },
                        )

                        AuriRoute.ChangePassword -> ChangePasswordScreen(
                            email = authStore.getEmail().orEmpty(),
                            token = token.orEmpty(),
                            onBack = { routeName = AuriRoute.Account.name },
                            onPasswordChanged = { result ->
                                authStore.save(result.token, result.email)
                                token = result.token
                            },
                        )

                        AuriRoute.Credits -> CreditsScreen(
                            token = token.orEmpty(),
                            onBack = { routeName = AuriRoute.Account.name },
                            onCheckout = { amount ->
                                rechargeAmount = amount
                                routeName = AuriRoute.CreditsCheckout.name
                            },
                        )

                        AuriRoute.CreditsCheckout -> CreditsCheckoutScreen(
                            token = token.orEmpty(),
                            amountYuan = rechargeAmount,
                            onBack = { routeName = AuriRoute.Credits.name },
                            onPaid = { routeName = AuriRoute.Credits.name },
                        )

                            AuriRoute.Chat -> ChatScreen(
                                onOpenSchedule = { routeName = AuriRoute.Schedule.name },
                                onOpenHealth = { routeName = AuriRoute.Health.name },
                                onOpenAccount = { routeName = AuriRoute.Account.name },
                                onOpenSettings = { routeName = AuriRoute.ChatSettings.name },
                                onOpenCredits = { routeName = AuriRoute.Credits.name },
                            )
                        }
                    }
                }

                if (BuildConfig.SELF_UPDATE_ENABLED) {
                    UpdateDialog(
                        state = updateUiState,
                        onDownload = { info -> startUpdateDownload(info) },
                        onInstall = { ready -> performInstall(ready) },
                        onDismiss = { updateUiState = UpdateUiState.Hidden },
                        onRetry = { retryUpdate() },
                    )
                }
            }
        }
        if (BuildConfig.SELF_UPDATE_ENABLED) {
            triggerUpdateCheck()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handledIntent = intent
        if (BuildConfig.SELF_UPDATE_ENABLED && intent.action == ACTION_CHECK_UPDATE) {
            triggerUpdateCheck()
        }
    }

    override fun onResume() {
        super.onResume()
        ensureKeepAlive()
    }

    private fun ensureKeepAlive() {
        if (!BuildConfig.KEEP_ALIVE_ENABLED) return
        val shouldRun = PrivacyConsent.isAgreed(this) &&
            AuthStore(applicationContext).getToken() != null &&
            areNotificationsGranted(this)
        if (shouldRun) {
            AuriKeepAliveController.start(this)
        } else {
            AuriKeepAliveController.stop(this)
        }
    }

    private fun triggerUpdateCheck(manual: Boolean = false, prompt: Boolean = true) {
        if (!BuildConfig.SELF_UPDATE_ENABLED) return
        if (updateUiState is UpdateUiState.Downloading || updateUiState is UpdateUiState.Ready) return
        updateChecker.check(manual = manual, prompt = prompt)
    }

    private fun startUpdateDownload(info: UpdateInfo) {
        lifecycleScope.launch {
            updateUiState = UpdateUiState.Downloading(info, 0L, 0L)
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val latest = updateManager.check()
                    val downloadInfo = if (latest.versionCode >= info.versionCode) latest else info
                    val file = updateManager.download(downloadInfo) { received, total ->
                        updateUiState = UpdateUiState.Downloading(downloadInfo, received, total)
                    }
                    downloadInfo to file
                }
            }
            result.fold(
                onSuccess = { (downloadInfo, file) ->
                    updateUiState = UpdateUiState.Ready(downloadInfo, file)
                },
                onFailure = { error ->
                    updateUiState = UpdateUiState.Error(info, error.message ?: "下载失败")
                },
            )
        }
    }

    private fun performInstall(ready: UpdateUiState.Ready) {
        if (!updateManager.canInstall()) {
            updateManager.openInstallPermissionSettings()
            return
        }
        runCatching { updateManager.install(ready.file) }
            .onFailure { error ->
                updateUiState = UpdateUiState.Error(ready.info, error.message ?: "无法启动安装")
            }
    }

    private fun retryUpdate() {
        val current = updateUiState
        if (current is UpdateUiState.Error && current.info != null) {
            startUpdateDownload(current.info)
        } else {
            updateUiState = UpdateUiState.Hidden
            triggerUpdateCheck(manual = true)
        }
    }
}

private enum class AuriRoute {
    Chat,
    Schedule,
    Health,
    Account,
    ChatSettings,
    ChangePassword,
    Credits,
    CreditsCheckout,
}
