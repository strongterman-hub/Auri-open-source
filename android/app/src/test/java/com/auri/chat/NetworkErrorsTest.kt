package com.auri.chat

import java.net.SocketTimeoutException
import java.net.UnknownHostException
import org.junit.Assert.*
import org.junit.Test

class NetworkErrorsTest {
    @Test fun translatesTransportErrorsWithoutLeakingInternalHosts() {
        assertEquals("网络响应超时，请稍后重试", readableNetworkError(SocketTimeoutException("timeout"), "失败"))
        assertEquals("暂时无法连接服务器，请检查网络后重试", readableNetworkError(UnknownHostException("internal-host"), "失败"))
        assertTrue(readableNetworkError(OfflineException(), "失败").contains("没有网络"))
    }
    @Test fun preservesActionableServerMessages() {
        assertEquals("Credits 不足", readableNetworkError(IllegalStateException("Credits 不足"), "失败"))
    }
}
