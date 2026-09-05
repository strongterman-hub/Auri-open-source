package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PasswordChangeTest {
    @Test
    fun validatesCodePasswordLengthAndConfirmation() {
        assertEquals(
            "请输入 6 位邮箱验证码",
            validatePasswordChange("12345", "new-password", "new-password"),
        )
        assertEquals(
            "新密码至少 6 位",
            validatePasswordChange("123456", "short", "short"),
        )
        assertEquals(
            "两次输入的密码不一致",
            validatePasswordChange("123456", "new-password", "different"),
        )
        assertNull(validatePasswordChange("123456", "new-password", "new-password"))
    }
}
