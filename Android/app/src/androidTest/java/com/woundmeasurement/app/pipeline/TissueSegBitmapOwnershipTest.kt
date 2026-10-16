package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import java.io.ByteArrayOutputStream
import org.junit.Assert.*
import org.junit.Test

class TissueSegBitmapOwnershipTest {
    private fun image(): Bitmap {
        val source = Bitmap.createBitmap(600, 800, Bitmap.Config.ARGB_8888)
        source.eraseColor(Color.RED)
        val bytes = ByteArrayOutputStream().also { source.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        source.recycle()
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
    }

    private fun classifyAndDraw(x: Int, y: Int, w: Int, h: Int, gw: Int, gh: Int) {
        val source = image()
        val before = source.getPixel(0, 0)
        repeat(3) {
            val classes = TissueSeg.classify(source, x, y, x+w, y+h, gw, gh,
                ByteArray(gw*gh) { 1 }, doubleArrayOf(1.0, 1.0, 1.0))
            assertNotNull(classes)
            assertEquals(gw*gh, classes!!.size)
            assertTrue(classes.all { it.toInt() in 1..5 })
            assertFalse("Classifier must not recycle its caller's bitmap", source.isRecycled)
            assertEquals(before, source.getPixel(0, 0))
            val target = Bitmap.createBitmap(600, 800, Bitmap.Config.ARGB_8888)
            Canvas(target).drawBitmap(source, 0f, 0f, null)
            assertEquals(before, target.getPixel(0, 0))
            target.recycle()
        }
        source.recycle()
    }

    @Test fun fullImmutableImageDownsampleKeepsOriginalDrawable() = classifyAndDraw(0, 0, 600, 800, 384, 512)
    @Test fun fullImmutableImageWithoutRescaleKeepsOriginalDrawable() = classifyAndDraw(0, 0, 600, 800, 600, 800)
    @Test fun croppedRescaledImageKeepsOriginalDrawable() = classifyAndDraw(40, 60, 300, 400, 150, 200)
    @Test fun croppedUnscaledImageKeepsOriginalDrawable() = classifyAndDraw(40, 60, 300, 400, 300, 400)
}
