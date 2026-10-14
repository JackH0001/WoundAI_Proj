package com.woundmeasurement.app.pipeline

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.graphics.Bitmap
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.*
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.flow.first
import okhttp3.*
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.Date
import java.util.UUID

class ClinicalResearchControllerTest {
    private class TestContext(base:Context):ContextWrapper(base) {
        val root=File(base.cacheDir,"research-controller-${UUID.randomUUID()}").apply{mkdirs()}
        override fun getApplicationContext():Context=this
        override fun getFilesDir()=File(root,"files").apply{mkdirs()}
        override fun getNoBackupFilesDir()=File(root,"no-backup").apply{mkdirs()}
        override fun getDatabasePath(name:String)=File(root,name)
        override fun getSharedPreferences(name:String,mode:Int):SharedPreferences=super.getSharedPreferences(root.name+name,mode)
        fun clear(){MedicalDataScope.entries.forEach{super.deleteSharedPreferences(root.name+it.settingsName)};root.deleteRecursively()}
    }
    private class Fixture(val ctx:TestContext) {
        var rejectReceipt=false;var serverStatus=200;var allowed=true
        val sent=mutableListOf<JSONObject>()
        val runtimes=mutableListOf<MedicalRuntime>()
        fun runtime():MedicalRuntime {
            val r=MedicalRuntime(ctx,MedicalDataScope.SYNTHETIC_REVIEW,{origin,current,_->
                val http=OkHttpClient.Builder().addInterceptor{chain->
                    val request=chain.request();val path=request.url.encodedPath
                    var code=200
                    val body=if(path.endsWith("/annotation")) {
                        val buffer=Buffer();request.body!!.writeTo(buffer);val payload=JSONObject(buffer.readUtf8());sent.add(payload)
                        code=serverStatus
                        JSONObject().put("status",if(sent.size==1) "enqueued" else "duplicate_skipped")
                            .put("code",payload.getString("code")).put("image_id",payload.getString("image_id"))
                            .put("mask_contract","woundai.annotation-masks/1").put("annotation_receipt_id","0123456789abcdef")
                            .put("wound_mask_sha256",payload.getString("wound_mask_sha256"))
                            .put("tissue_mask_sha256",if(rejectReceipt) "0".repeat(64) else sha(android.util.Base64.decode(payload.getString("tissue_mask_png"),android.util.Base64.NO_WRAP))).toString()
                    } else {
                        val perms=if(allowed) "[\"measure.sample\",\"gt.verify\",\"annotation.submit\"]" else "[\"measure.sample\"]"
                        """{"access_token":"synthetic-token","session_contract":"account-revision-v1","identity":"mmh-review-demo:dr01","org":"mmh-review-demo","user":"dr01","role":"physician","role_zh":"醫師","perms":$perms,"expires_at":9999999999}"""
                    }
                    Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(code).message("Synthetic").body(body.toResponseBody()).build()
                }.build()
                BackendClient(origin,"",http,current,false,"mmh-review-demo")
            })
            runtimes.add(r);return r
        }
        private fun sha(bytes:ByteArray)=java.security.MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it)}
        suspend fun saved(r:MedicalRuntime):MeasurementEntity {
            val bitmap=Bitmap.createBitmap(64,96,Bitmap.Config.ARGB_8888).apply{eraseColor(0xff996644.toInt())}
            val image=try{requireNotNull(r.images.save(bitmap))}finally{bitmap.recycle()}
            val mask=ByteArray(64){if(it==18)0 else 1}
            val raster=EditRaster(mask,ByteArray(64){1},mask.copyOf(),4f,6f,8,8,1f,.1,maskPx=63,canvasW=64,canvasH=96,autoBaseline=ByteArray(64){1})
            val (bytes,meta)=requireNotNull(EditRasterCodec.encode(raster))
            val row=MeasurementEntity(patientId=null,timestamp=Date(1234),hasWound=true,confidence=1.0,estimatedArea=6.3,
                estimatedVolume=null,woundType=null,quality="synthetic",processingTime=0,imagePath=image,dataPath="",
                imageId="synthetic-image",wdCode="synthetic-code",source="phantom",imageW=64,imageH=96,
                gtPolygon="[[4,6],[12,6],[12,14],[4,14]]",doctorVerified=true,exudate=1,
                rasterPath=requireNotNull(r.images.save(bytes)),rasterMeta=meta,
                annotationProvenance=AnnotationProvenance("synthetic",mapOf("quality" to .9),"synthetic confirmation").encode())
            return row.copy(id=r.database.measurementDao().insertMeasurement(row))
        }
    }
    private fun fixture(block:suspend Fixture.(MedicalRuntime,MeasurementEntity)->Unit)=runBlocking {
        val ctx=TestContext(ApplicationProvider.getApplicationContext());val f=Fixture(ctx);val r=f.runtime()
        try {assertTrue(r.setCredentials("dr01",UUID.randomUUID().toString()));f.block(r,f.saved(r))}
        finally {f.runtimes.forEach{it.deactivate()};r.database.close();ctx.clear()}
    }
    @Test fun onlyExplicitSubmitExportsConfirmedSnapshotAndRetryDoesNotRepeat()=fixture{r,row->
        val exports=PrivateExports(ctx,r.scope);exports.enabled=true
        assertTrue(exports.list().isEmpty()) // A saved timeline row alone does not export.
        val status=r.research.enqueue(row);assertTrue(status.contains("行政影像"))
        val first=exports.list().single();val files=exports.read(first)
        assertArrayEquals(r.images.rawBytes(row.imagePath),files["measurement.jpg"])
        r.research.drainOnce()
        assertArrayEquals(android.util.Base64.decode(sent.single().getString("wound_mask_png"),android.util.Base64.NO_WRAP),files["wound.png"])
        assertArrayEquals(android.util.Base64.decode(sent.single().getString("tissue_mask_png"),android.util.Base64.NO_WRAP),files["tissue.png"])
        val meta=JSONObject(String(files.getValue("result.json")))
        assertEquals(sent.single().getDouble("area_cm2"),meta.getDouble("area_cm2"),0.0)
        r.research.drainOnce();assertEquals(listOf(first),exports.list())
        val current=r.database.measurementDao().getById(row.id)!!
        val revised=current.copy(exudate=2,annotationSubmitted=false)
        assertTrue(r.database.measurementDao().updateIfUnchanged(current,revised))
        r.research.enqueue(revised);r.research.drainOnce()
        assertEquals(2,exports.list().size)
        assertEquals(2,sent.last().getInt("exudate"))
        assertArrayEquals(files.getValue("result.json"),exports.read(first).getValue("result.json"))
    }
    @Test fun inaccessibleCustomFolderDoesNotLoseQueuedUploadOrPrivateCopy()=fixture{r,row->
        val exports=PrivateExports(ctx,r.scope);exports.enabled=true
        ctx.getSharedPreferences(r.scope.settingsName,Context.MODE_PRIVATE).edit()
            .putString("administrative_exports.folder","content://unavailable.test/tree/folder").commit()
        val text=r.research.enqueue(row)
        assertTrue(text.contains("自訂資料夾輸出失敗"));assertEquals(1,exports.list().size)
        r.research.drainOnce();assertTrue(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
        exports.usePrivateFolder();assertNull(exports.outputFolder)
    }
    @Test fun savedJobUsesActualBackendEncoderAndReceiptBeforeMarking()=fixture{r,row->
        val text=r.research.enqueue(row);assertTrue(text.contains("尚未確認雲端保存"));assertTrue(sent.isEmpty())
        r.research.drainOnce()
        assertEquals(1,sent.size);val body=sent.single()
        assertEquals(row.imageId,body.getString("image_id"));assertEquals(row.exudate,body.getInt("exudate"))
        assertEquals("woundai.annotation-masks/1",body.getString("mask_contract"));assertTrue(body.has("wound_mask_png"));assertTrue(body.has("tissue_mask_png"))
        assertTrue(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
        assertEquals(0,r.research.status.value.pending)
        assertTrue(r.research.status.value.items.single().text.contains("本筆無深度"))
    }
    @Test fun runtimeRestartResumesPersistedJobWithoutManualReenqueue()=fixture{r,row->
        r.research.enqueue(row);r.deactivate()
        val next=runtime();next.research.drainOnce()
        assertEquals(1,sent.size);assertTrue(next.database.measurementDao().getById(row.id)!!.annotationSubmitted)
    }
    @Test fun foregroundWorkerCompletesQueuedJobWithoutManualDrain()=fixture{r,row->
        r.research.setForeground(true)
        r.research.enqueue(row)
        withTimeout(5000){r.research.status.first {state->state.items.any{it.state==ClinicalAnnotationOutbox.State.COMPLETE}}}
        assertEquals(1,sent.size)
        assertTrue(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
    }
    @Test fun permissionRevocationPreventsQueuedRequest()=fixture{r,row->
        r.research.enqueue(row);r.addPendingWithdrawals(listOf(row.wdCode!!));r.research.drainOnce()
        assertTrue(sent.isEmpty());assertFalse(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
        assertEquals(ClinicalAnnotationOutbox.State.CANCELLED,r.research.status.value.items.single().state)
    }
    @Test fun switchingConfiguredAccountDoesNotSendOriginalDoctorsJob()=fixture{r,row->
        r.research.enqueue(row);r.setCredentials("dr02",UUID.randomUUID().toString());r.research.drainOnce()
        assertTrue(sent.isEmpty());assertEquals(1,r.research.status.value.pending)
    }
    @Test fun invalidReceiptPausesWithoutMarkingAndCanBeExplicitlyRetried()=fixture{r,row->
        r.research.enqueue(row);rejectReceipt=true;r.research.drainOnce()
        assertEquals(ClinicalAnnotationOutbox.State.PAUSED,r.research.status.value.items.single().state)
        assertFalse(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
        rejectReceipt=false;r.research.drainOnce();assertEquals(1,sent.size)
        r.research.drainOnce(true);assertEquals(2,sent.size);assertTrue(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
    }
    @Test fun serverFailureKeepsJobAndExplicitRetryReusesSameMask()=fixture{r,row->
        r.research.enqueue(row);serverStatus=503;r.research.drainOnce()
        assertFalse(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
        val first=sent.single();serverStatus=200;r.research.drainOnce(true)
        assertEquals(first.getString("wound_mask_png"),sent.last().getString("wound_mask_png"))
        assertEquals(first.getString("tissue_mask_png"),sent.last().getString("tissue_mask_png"))
        assertTrue(r.database.measurementDao().getById(row.id)!!.annotationSubmitted)
    }
    @Test fun savedNewRevisionIsNotAcknowledgedByOldQueuedJob()=fixture{r,row->
        r.research.enqueue(row);val newer=row.copy(exudate=3)
        assertTrue(r.database.measurementDao().updateIfUnchanged(row,newer));r.research.drainOnce()
        assertEquals(1,sent.single().getInt("exudate"));assertEquals(newer,r.database.measurementDao().getById(row.id))
        assertTrue(r.research.status.value.items.single().text.contains("較新修訂未標記"))
    }
    @Test fun missingPhysicianPermissionCannotEnqueue()=fixture{r,row->
        allowed=false
        try {r.research.enqueue(row);fail("unprivileged actor queued")}
        catch(_:IllegalArgumentException){}
        assertTrue(sent.isEmpty());assertTrue(ClinicalAnnotationOutbox(ctx,r.scope).jobs().isEmpty())
    }
}
