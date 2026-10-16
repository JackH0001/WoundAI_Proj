package com.woundmeasurement.app.pipeline

import android.graphics.Bitmap
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class EditorInteractionRegressionTest {
    @get:Rule val compose=createComposeRule()
    private fun raster()=EditRaster(ByteArray(32*32){1},ByteArray(32*32){1},ByteArray(32*32){1},
        96f,96f,32,32,1f,.01,maskPx=1024,canvasW=256,canvasH=256,autoBaseline=ByteArray(32*32){1})
    private fun open(done:(EditRaster)->Unit) {
        val bitmap=Bitmap.createBitmap(256,256,Bitmap.Config.ARGB_8888).apply{eraseColor(0xffaa6655.toInt())}
        compose.setContent {MaterialTheme {WoundEditScreen(bitmap,listOf(listOf(96,96),listOf(128,96),listOf(128,128),listOf(96,128)),
            originalArea=10.24,tissueFrac=mapOf("granulation" to 1.0),exudate=0,mmPerPx=1.0,resume=raster(),
            onCancel={},onDone={_,_,_,_,_,r->done(r)})}}
    }
    @Test fun combinedPinchAndPanKeepsPreviousImageAnchorUnderNewCentroid() {
        val oldOffset=Offset(12f,9f);val oldScale=2f;val newScale=3f
        val oldCentroid=Offset(100f,120f);val newCentroid=Offset(140f,150f)
        val imageAnchor=oldOffset+oldCentroid/oldScale
        val result=editorPinchOffset(oldOffset,oldScale,newScale,newCentroid,newCentroid-oldCentroid)
        val screenAfter=(imageAnchor-result)*newScale
        assertEquals(newCentroid.x,screenAfter.x,.001f);assertEquals(newCentroid.y,screenAfter.y,.001f)
    }
    @Test fun pinchBeginningOutsideRoiDoesNotPaintOrChangeOriginalGeometry() {
        var result:EditRaster?=null;open{result=it}
        compose.onNodeWithTag("wound-edit-canvas").performTouchInput {
            down(0,Offset(width*.05f,height*.5f));advanceEventTime(32)
            down(1,Offset(width*.65f,height*.5f));advanceEventTime(32)
            moveTo(0,Offset(width*.10f,height*.45f));moveTo(1,Offset(width*.8f,height*.55f))
            up(0);up(1)
        }
        compose.onNodeWithText("完成修邊").performClick()
        compose.runOnIdle {
            val r=requireNotNull(result);assertEquals(32,r.mw);assertEquals(32,r.mh)
            assertEquals(96f,r.rx0);assertEquals(96f,r.ry0)
            assertArrayEquals(raster().mask,r.mask);assertArrayEquals(raster().tissue,r.tissue)
        }
    }
    @Test fun drawingAndUndoKeepCanvasBoundsAndRestoreExpandedRoi() {
        var result:EditRaster?=null;open{result=it}
        val canvas=compose.onNodeWithTag("wound-edit-canvas")
        val before=canvas.fetchSemanticsNode().boundsInRoot
        canvas.performTouchInput {click(Offset(width*.05f,height*.5f))}
        assertEquals(before,canvas.fetchSemanticsNode().boundsInRoot)
        compose.onNodeWithContentDescription("復原").performClick()
        compose.onNodeWithText("完成修邊").performClick()
        compose.runOnIdle {val r=requireNotNull(result);assertEquals(32,r.mw);assertEquals(96f,r.rx0);assertArrayEquals(raster().mask,r.mask)}
    }
}
