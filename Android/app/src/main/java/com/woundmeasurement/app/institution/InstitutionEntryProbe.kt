package com.woundmeasurement.app.institution

import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.SocketException
import java.net.UnknownHostException
import kotlinx.coroutines.delay

/** Retry only the read-only health request with the same credential. Never replay login/upload. */
internal object InstitutionEntryProbe {
    suspend fun verify(
        probe: suspend () -> Pair<Int, String>,
        wait: suspend (Long) -> Unit = { delay(it) },
        onBusy: () -> Unit = {}
    ) {
        for (attempt in 0..2) {
            val response = try { probe() } catch (e: IOException) {
                // TLS, invalid routing/token and other validation failures never retry.
                val transient = e is InterruptedIOException || e is ConnectException ||
                    e is SocketException || e is UnknownHostException
                if (!transient) throw e
                if (attempt == 2) throw IOException("Google 已登入，但機構入口連線未完成；請確認網路後重試，無需修改 WoundAI 密碼")
                null
            }
            if (response == null || response.first in listOf(429, 502, 503, 504)) {
                if (attempt == 2) throw IOException("Google 已登入，但機構入口仍忙碌；請稍後再試，無需修改 WoundAI 密碼")
                onBusy()
                wait((attempt + 1) * 2000L)
                continue
            }
            InstitutionSession.verifyHealth(response.first, response.second)
            return
        }
    }
}
