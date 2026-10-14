package com.woundmeasurement.app.institution

import java.io.IOException
import kotlinx.coroutines.delay

/** Only the read-only entry health probe may retry. Never replay login/upload writes. */
internal object InstitutionEntryProbe {
    suspend fun verify(
        probe: suspend () -> Pair<Int, String>,
        wait: suspend (Long) -> Unit = { delay(it) },
        onBusy: () -> Unit = {}
    ) {
        for (attempt in 0..2) {
            val (code, body) = probe()
            if (code == 429 || code == 503) {
                if (attempt == 2) throw IOException("Google 已登入，但機構入口仍忙碌；請稍後再試，無需修改 WoundAI 密碼")
                onBusy()
                wait((attempt + 1) * 2000L)
                continue
            }
            InstitutionSession.verifyHealth(code, body)
            return
        }
    }
}
