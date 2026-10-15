package com.woundmeasurement.app.pipeline

/** Main-thread focus request identity: late callbacks must never confirm a newer tap. */
internal class CameraFocusFeedback {
    enum class Phase(val label: String) {
        READY("點選傷口位置對焦"),
        FOCUSING("正在對焦…"),
        CONFIRMED("對焦完成；請確認影像清晰再拍攝"),
        UNCONFIRMED("未確認對焦，請調整距離或光線後再點選"),
        UNSUPPORTED("此鏡頭不支援點選自動對焦；請調整距離並確認影像清晰")
    }
    var generation: Long = 0; private set
    var phase = Phase.READY; private set
    fun begin(supported: Boolean): Long {
        generation++
        phase = if (supported) Phase.FOCUSING else Phase.UNSUPPORTED
        return generation
    }
    fun complete(request: Long, successful: Boolean): Phase {
        if (request == generation && phase == Phase.FOCUSING)
            phase = if (successful) Phase.CONFIRMED else Phase.UNCONFIRMED
        return phase
    }
    fun expire(request: Long): Phase {
        if (request == generation) { generation++; phase = Phase.READY }
        return phase
    }
    fun cancel(): Phase { generation++; phase = Phase.READY; return phase }
}
