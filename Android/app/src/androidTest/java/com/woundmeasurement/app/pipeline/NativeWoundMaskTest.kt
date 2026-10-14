package com.woundmeasurement.app.pipeline

import android.graphics.BitmapFactory
import android.graphics.Color
import android.util.Base64
import org.junit.Assert.*
import org.junit.Test

class NativeWoundMaskTest {
    @Test fun pngPreservesEveryPixelIncludingHoleAndIsland() {
        val mask = ByteArray(64) { if (it % 8 in 1..6 && it / 8 in 1..6) 1 else 0 }
        mask[27] = 0; mask[0] = 1
        val raw = Base64.decode(WoundMaskCodec.encode(mask,8,8)!!,Base64.DEFAULT)
        val bitmap = BitmapFactory.decodeByteArray(raw,0,raw.size)
        assertEquals(8,bitmap.width);assertEquals(8,bitmap.height)
        for (i in mask.indices) {
            val pixel=bitmap.getPixel(i % 8,i / 8)
            assertEquals(mask[i].toInt(),Color.red(pixel));assertEquals(0,Color.green(pixel))
            assertEquals(0,Color.blue(pixel));assertEquals(255,Color.alpha(pixel))
        }
        bitmap.recycle()
    }
    @Test fun malformedOrEmptyMaskIsNotSilentlyUploaded() {
        for (mask in listOf(ByteArray(64),ByteArray(64) { 2 },ByteArray(63) { 1 }))
            assertNull(WoundMaskCodec.encode(mask,8,8))
        assertNull(WoundMaskCodec.encode(ByteArray(64) { 1 },0,8))
    }
}
