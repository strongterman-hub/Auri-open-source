package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ResetPasswordTest {
    @Test
    fun validatesEmailCodeAndMatchingPassword() {
        assertEquals(
            "请输入有效邮箱",
            validatePasswordReset("bad", "123456", "secret1", "secret1"),
        )
        assertEquals(
            "请输入 6 位邮箱验证码",
            validatePasswordReset("user@example.com", "123", "secret1", "secret1"),
        )
        assertEquals(
            "两次输入的密码不一致",
            validatePasswordReset("user@example.com", "123456", "secret1", "secret2"),
        )
        assertNull(
            validatePasswordReset("user@example.com", "123456", "secret1", "secret1"),
        )
    }
}
