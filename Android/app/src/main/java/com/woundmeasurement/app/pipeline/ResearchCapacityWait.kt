package com.woundmeasurement.app.pipeline

import com.google.gson.JsonParser
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import kotlin.math.max
import kotlin.random.Random

/** Scheduling only. Never mutates an image, mask, research revision or receipt. */
internal class ResearchCapacityException(val notBefore:Long):java.io.IOException("後端容量暫滿或服務暫不可用；請依待傳狀態稍候再試")

internal object ResearchCapacityWait {
    fun deadline(retryAfter:String?, serverDate:String?, body:String, now:Long,
                 minimumMs:Long=60_000, jitterMs:Long=Random.nextLong(0,5_001)):Long {
        fun seconds(text:String?):Double? = text?.trim()?.toDoubleOrNull()?.takeIf { it.isFinite() && it>=0 }
        fun date(text:String?):Long? = try {
            text?.let { SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss zzz",Locale.US).apply {
                timeZone=TimeZone.getTimeZone("GMT");isLenient=false
            }.parse(it)?.time }
        } catch(_:Exception) { null }
        var delay=minimumMs.coerceAtLeast(0).toDouble()
        seconds(retryAfter)?.let { delay=max(delay,it*1000) }
            ?: date(retryAfter)?.let { delay=max(delay,it.toDouble()-(date(serverDate)?:now).toDouble()) }
        try {
            val n=JsonParser.parseString(body).asJsonObject.get("retry_after")
            if(n?.isJsonPrimitive==true && n.asJsonPrimitive.isNumber) {
                seconds(n.asString)?.let { delay=max(delay,it*1000) }
            }
        } catch(_:Exception) { }
        val total=now.toDouble()+delay+jitterMs.coerceAtLeast(0)
        return if(!total.isFinite() || total>=Long.MAX_VALUE.toDouble()) Long.MAX_VALUE else total.toLong()
    }
}
