package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test

class EditAreaPolicyTest {
    private fun raster(mask: ByteArray = byteArrayOf(1,1,1,1), tissue: ByteArray = byteArrayOf(1,1,1,1),
                       cm: Double = 0.1, x: Float = 1f, y: Float = 1f, w: Int = 2, h: Int = 2) =
        EditRaster(mask,tissue,ByteArray(mask.size),x,y,w,h,1f,cm,canvasW=20,canvasH=20)
    @Test fun unchangedOrTissueOnlyPreservesOriginalAcrossReopening() {
        var entry = raster()
        repeat(5) {
            val current = raster(tissue=byteArrayOf(2,3,4,5))
            assertEquals(9.0, EditAreaPolicy.area(9.0,entry,current)!!,0.0)
            entry = current
        }
    }
    @Test fun singlePixelChangeRecomputesAndUndoRestoresOriginal() {
        val entry=raster(); val current=raster(mask=byteArrayOf(1,1,1,0))
        assertEquals(0.3,EditAreaPolicy.area(9.0,entry,current)!!,1e-12)
        current.mask[3]=1
        assertEquals(9.0,EditAreaPolicy.area(9.0,entry,current)!!,0.0)
    }
    @Test fun recalibrationRecomputesEvenWithIdenticalBoundary() {
        assertEquals(1.6,EditAreaPolicy.area(9.0,raster(),raster(cm=0.4))!!,1e-12)
    }
    @Test fun paddingIsNotAnEditButTranslationIs() {
        val entry=raster()
        val padded=raster(mask=byteArrayOf(0,0,0,0,0,1,1,0,0,1,1,0,0,0,0,0),x=0f,y=0f,w=4,h=4)
        assertEquals(9.0,EditAreaPolicy.area(9.0,entry,padded)!!,0.0)
        assertEquals(0.4,EditAreaPolicy.area(9.0,entry,raster(x=2f))!!,1e-12)
    }
    @Test fun missingOriginalUsesCalculatedArea() {
        assertEquals(0.4,EditAreaPolicy.area(null,raster(),raster())!!,1e-12)
    }
}
