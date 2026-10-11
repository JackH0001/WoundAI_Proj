package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import java.io.ByteArrayOutputStream
import org.junit.Assert.*
import org.junit.Test

/** Pixel-exact asymmetric fixture, not an AI or physical-camera accuracy claim. */
class SyntheticGeometryTest {
    private fun pixels(): IntArray = IntArray(128*96) { n ->
        val x=n%128; val y=n/128
        if ((x in 10..29 && y in 10..69)||(x in 30..59 && y in 50..69)) Color.RED else Color.WHITE
    }
    @Test fun pngRoundtripKeepsAsymmetricGeometry() {
        val original=pixels(); val bmp=Bitmap.createBitmap(original,128,96,Bitmap.Config.ARGB_8888)
        val bytes=ByteArrayOutputStream().also { assertTrue(bmp.compress(Bitmap.CompressFormat.PNG,100,it)) }.toByteArray()
        val decoded=BitmapFactory.decodeByteArray(bytes,0,bytes.size)
        val out=IntArray(128*96); decoded.getPixels(out,0,128,0,0,128,96)
        assertArrayEquals(original,out); assertEquals(1800,out.count { it==Color.RED })
        assertEquals(Color.RED,out[60*128+50]); assertEquals(Color.WHITE,out[20*128+50])
        bmp.recycle(); decoded.recycle()
    }
    @Test fun syntheticKnownScaleAndPush() {
        val red=pixels().count { it==Color.RED }
        // 20mm marker occupies 400px -> each pixel is 1mm². L shape is 18cm².
        val area=WoundPipeline.areaCm2ByRatio(red,400.0,20.0)
        assertEquals(18.0,area!!,1e-9)
        val push=WoundPipeline.push(area,mapOf("granulation" to 1.0),2)
        assertEquals(9,push.area); assertEquals(2,push.tissue); assertEquals(13,push.full)
    }
    @Test fun noCalibrationDoesNotInventAreaOrFullScore() {
        val result=WoundPipeline.analyze(CaptureContainer(rgb=byteArrayOf(),timestamp="2026-10-11T00:00:00Z"),1800,null,mapOf("granulation" to 1.0),1.0,2)
        assertNull(result.areaCm2); assertNull(result.push.full)
        assertNull(WoundPipeline.areaCm2ByRatio(1800,0.0,20.0))
    }
}
