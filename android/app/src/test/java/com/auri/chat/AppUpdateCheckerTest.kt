package com.auri.chat

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class AppUpdateCheckerTest {
    private fun release(code: Int) = UpdateInfo(code, "test-$code", false, null, null, 0, "")

    @Test
    fun manualTapJoinsAutomaticRequestAndCanReopenDismissedUpdate() = runBlocking {
        val response = CompletableDeferred<UpdateInfo>()
        var requests = 0
        val results = mutableListOf<Pair<Boolean, Boolean>>()
        val checker = AppUpdateChecker(26, this, {
            requests++
            response.await()
        }) { _, manual, prompt -> results += manual to prompt }
        checker.check(prompt = false)
        checker.check(manual = true)
        yield()
        assertEquals(1, requests)
        assertTrue(checker.state.value.checking)
        response.complete(release(27))
        yield()
        assertEquals(listOf(true to true), results)
        assertEquals(27, checker.state.value.available?.versionCode)
        // Closing a dialog does not touch the independently held discovery state.
        checker.check(prompt = false)
        yield()
        assertEquals(false to false, results.last())
        assertEquals(27, checker.state.value.available?.versionCode)
        checker.check(manual = true)
        yield()
        assertEquals(true to true, results.last())
    }

    @Test
    fun networkFailureRetainsBadgeAndSuccessfulRetryClearsError() = runBlocking {
        var fail = false
        val checker = AppUpdateChecker(26, this, {
            if (fail) error("offline") else release(27)
        }) { _, _, _ -> }
        checker.check()
        yield()
        fail = true
        checker.check(manual = true)
        yield()
        assertTrue(checker.state.value.failed)
        assertFalse(checker.state.value.checking)
        assertEquals(27, checker.state.value.available?.versionCode)
        fail = false
        checker.check(manual = true)
        yield()
        assertFalse(checker.state.value.failed)
        assertEquals(27, checker.state.value.available?.versionCode)
    }

    @Test
    fun equalOrOlderReleaseIsNotOfferedAsUpdate() = runBlocking {
        var code = 28
        val checker = AppUpdateChecker(27, this, { release(code) }) { _, _, _ -> }
        checker.check()
        yield()
        assertNotNull(checker.state.value.available)
        for (published in listOf(27, 26)) {
            code = published
            checker.check(manual = true)
            yield()
            assertNull(checker.state.value.available)
            assertTrue(checker.state.value.checked)
            assertFalse(checker.state.value.failed)
        }
    }
}
