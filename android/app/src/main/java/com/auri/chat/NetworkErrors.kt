package com.auri.chat

import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

internal class OfflineException : IOException("当前没有网络，请连接网络后点击重试")

internal fun readableNetworkError(error: Throwable, fallback: String): String = when (error) {
    is OfflineException -> error.message.orEmpty()
    is SocketTimeoutException -> "网络响应超时，请稍后重试"
    is UnknownHostException, is ConnectException -> "暂时无法连接服务器，请检查网络后重试"
    is SSLException -> "无法建立安全连接，请检查网络和设备时间后重试"
    is IOException -> "网络连接中断，请稍后重试"
    else -> error.message?.takeIf { it.isNotBlank() } ?: fallback
}
