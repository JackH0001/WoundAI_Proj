package com.woundmeasurement.app.pipeline

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.graphics.Bitmap
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.*
import kotlinx.coroutines.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.Date
import java.util.UUID

class ClinicalAnnotationOutboxTest {
    private class TestContext(base:Context):ContextWrapper(base) {
        val root=File(base.cacheDir,"outbox-${UUID.randomUUID()}").apply{mkdirs()}
        override fun getApplicationContext():Context=this
        override fun getFilesDir()=File(root,"files").apply{mkdirs()}
        override fun getNoBackupFilesDir()=File(root,"no-backup").apply{mkdirs()}
        override fun getDatabasePath(name:String)=File(root,name)
        override fun getSharedPreferences(name:String,mode:Int):SharedPreferences=super.getSharedPreferences(root.name+name,mode)
        fun clear(){ MedicalDataScope.entries.forEach{super.deleteSharedPreferences(root.name+it.settingsName)};root.deleteRecursively() }
    }
    private val binding=ClinicalAnnotationOutbox.Binding("https://synthetic.invalid","synthetic-user","synthetic-actor")
    private class Fixture(val ctx:TestContext,val runtime:MedicalRuntime,val row:MeasurementEntity,val image:ByteArray,val raster:ByteArray) {
        var clock=1_000_000L
        fun queue(scope:MedicalDataScope=MedicalDataScope.MMH)=ClinicalAnnotationOutbox(ctx,scope){clock}
        suspend fun enqueue(queue:ClinicalAnnotationOutbox,binding:ClinicalAnnotationOutbox.Binding)=queue.enqueue(row,binding,image,raster,
            current={runtime.database.measurementDao().getById(row.id)},savedAssets={image to raster},permitted={ClinicalAnnotationOutbox.Permission.ALLOW})
    }
    private fun fixture(block:suspend Fixture.()->Unit)=runBlocking {
        val ctx=TestContext(ApplicationProvider.getApplicationContext());val runtime=MedicalRuntime(ctx)
        try {
            val bitmap=Bitmap.createBitmap(64,96,Bitmap.Config.ARGB_8888).apply{eraseColor(0xff996644.toInt())}
            val imagePath=try{requireNotNull(runtime.images.save(bitmap))}finally{bitmap.recycle()}
            val mask=ByteArray(64){if(it==18)0 else 1}
            val edit=EditRaster(mask,ByteArray(64){1},mask.copyOf(),4f,6f,8,8,1f,.1,maskPx=63,canvasW=64,canvasH=96,autoBaseline=ByteArray(64){1})
            val (bytes,meta)=requireNotNull(EditRasterCodec.encode(edit))
            val row=MeasurementEntity(patientId=null,timestamp=Date(1234),hasWound=true,confidence=1.0,estimatedArea=6.3,
                estimatedVolume=null,woundType=null,quality="synthetic",processingTime=0,imagePath=imagePath,dataPath="",
                imageId="synthetic-image",wdCode="synthetic-code",source="phantom",imageW=64,imageH=96,
                gtPolygon="[[4,6],[12,6],[12,14],[4,14]]",doctorVerified=true,exudate=1,
                rasterPath=requireNotNull(runtime.images.save(bytes)),rasterMeta=meta,
                annotationProvenance=AnnotationProvenance("synthetic",mapOf("quality" to 1.0),null).encode())
            val saved=row.copy(id=runtime.database.measurementDao().insertMeasurement(row))
            Fixture(ctx,runtime,saved,requireNotNull(runtime.images.rawBytes(imagePath)),bytes).block()
        } finally {runtime.deactivate();runtime.database.close();ctx.clear()}
    }
    private fun receipt(job:ClinicalAnnotationOutbox.Job,status:String="enqueued")=ClinicalAnnotationOutbox.Response(200,JSONObject()
        .put("code",job.measurement().wdCode).put("image_id",job.measurement().imageId).put("status",status)
        .put("annotation_receipt_id","0123456789abcdef").put("mask_contract","woundai.annotation-masks/1")
        .put("wound_mask_sha256",job.woundSHA).put("tissue_mask_sha256",job.tissueSHA).toString())
    private val allow:suspend(ClinicalAnnotationOutbox.Job)->ClinicalAnnotationOutbox.Permission={ClinicalAnnotationOutbox.Permission.ALLOW}

    @Test fun encryptedRestartRetainsExactFrozenSnapshotAndTimestamp()=fixture {
        val q=queue();val job=enqueue(q,binding);val loaded=queue().jobs().single()
        assertEquals(job,loaded);assertEquals(row,loaded.measurement());assertArrayEquals(raster,loaded.rasterBytes())
        assertEquals(1234,loaded.measurement().timestamp.time)
        val disk=File(q.directory,"${job.id}.enc").readBytes()
        assertFalse(String(disk).contains("synthetic-user"));assertFalse(String(disk).contains("synthetic-image"))
        assertTrue(q.directory.canonicalPath.startsWith(ctx.noBackupFilesDir.canonicalPath))
    }
    @Test fun explicitSameRevisionQueuesOnceButDifferentActorDoesNotInherit()=fixture {
        val q=queue();assertEquals(enqueue(q,binding).id,enqueue(queue(),binding).id)
        assertNotEquals(enqueue(q,binding).id,enqueue(q,binding.copy(actor="another-actor")).id)
        assertEquals(2,q.jobs().size)
    }
    @Test fun changedOriginUserOrActorNeverSendsOrAcknowledges()=fixture {
        val q=queue();val job=enqueue(q,binding)
        for(other in listOf(binding.copy(origin="https://other.invalid"),binding.copy(configuredUser="other"),binding.copy(actor="other"))) {
            val result=q.process(job.id,other,allow,upload={error("must not upload")},acknowledge={error("must not ack")})
            assertEquals(ClinicalAnnotationOutbox.State.PENDING,result.state)
        }
    }
    @Test fun missingOrChangedSavedRowCannotEnterQueue()=fixture {
        val q=queue()
        for(snapshot in listOf<MeasurementEntity?>(null,row.copy(exudate=3))) {
            try {q.enqueue(row,binding,image,raster,current={snapshot},savedAssets={image to raster},permitted={ClinicalAnnotationOutbox.Permission.ALLOW});fail("accepted stale row")}
            catch(_:IllegalArgumentException){}
        }
        assertTrue(q.jobs().isEmpty())
    }
    @Test fun suppliedMaskMustMatchSavedAssetsBeforeEnqueue()=fixture {
        val q=queue()
        try {q.enqueue(row,binding,image,raster,current={row},savedAssets={image to byteArrayOf(0,1)},
            permitted={ClinicalAnnotationOutbox.Permission.ALLOW});fail("unsaved raster queued")}
        catch(_:IllegalArgumentException){}
        assertTrue(q.jobs().isEmpty())
    }
    @Test fun assetChangeDuringEnqueueCannotCommitJob()=fixture {
        val q=queue();var reads=0
        try {q.enqueue(row,binding,image,raster,current={row},savedAssets={if(reads++==0) image to raster else image to byteArrayOf(1)},
            permitted={ClinicalAnnotationOutbox.Permission.ALLOW});fail("changing raster queued")}
        catch(_:IllegalArgumentException){}
        assertTrue(q.jobs().isEmpty())
    }
    @Test fun withdrawnPermissionStopsBeforeNetwork()=fixture {
        val q=queue();val job=enqueue(q,binding)
        val result=q.process(job.id,binding,{ClinicalAnnotationOutbox.Permission.REVOKED},upload={error("must not upload")},acknowledge={error("must not ack")})
        assertEquals(ClinicalAnnotationOutbox.State.CANCELLED,result.state)
    }
    @Test fun temporaryAuthorizationWaitRequiresExplicitResume()=fixture {
        val q=queue();val job=enqueue(q,binding)
        assertEquals(ClinicalAnnotationOutbox.State.PAUSED,q.process(job.id,binding,{ClinicalAnnotationOutbox.Permission.WAIT},{error("no")},{error("no")}).state)
        assertEquals(ClinicalAnnotationOutbox.State.PAUSED,q.process(job.id,binding,allow,{error("no")},{error("no")}).state)
        assertEquals(ClinicalAnnotationOutbox.State.COMPLETE,q.process(job.id,binding,allow,{receipt(it)},{true},retryPaused=true).state)
    }
    @Test fun timeoutPersistsBackoffAndRestartRetriesSameJob()=fixture {
        val q=queue();val job=enqueue(q,binding);var requests=0
        val failed=q.process(job.id,binding,allow,{requests++;throw java.io.IOException("synthetic timeout")},{error("no ack")})
        assertEquals(ClinicalAnnotationOutbox.State.RETRY,failed.state);assertEquals(1,failed.attempts)
        assertEquals(clock+60_000,failed.nextAttemptAt)
        queue().process(job.id,binding,allow,{requests++;receipt(it)},{true});assertEquals(1,requests)
        clock+=60_000
        val done=queue().process(job.id,binding,allow,{requests++;assertEquals(job.rowJson,it.rowJson);receipt(it,"duplicate_skipped")},{true})
        assertEquals(2,requests);assertTrue(done.localAcknowledged)
    }
    @Test fun badReceiptNeverMarksRecordAndNeverClaimsComplete()=fixture {
        val q=queue();val job=enqueue(q,binding)
        val good=JSONObject(receipt(job).body)
        val bad=ClinicalAnnotationOutbox.Response(200,good.put("tissue_mask_sha256","0".repeat(64)).toString())
        val result=q.process(job.id,binding,allow,{bad},{error("invalid receipt must not ack")})
        assertEquals(ClinicalAnnotationOutbox.State.PAUSED,result.state);assertNull(result.receiptID)
    }
    @Test fun serverTransientFailureSchedulesRetryButValidationFailurePauses()=fixture {
        val q=queue();val job=enqueue(q,binding)
        val first=q.process(job.id,binding,allow,{ClinicalAnnotationOutbox.Response(503,"unavailable")},{error("no")})
        assertEquals(ClinicalAnnotationOutbox.State.RETRY,first.state);clock+=60_000
        val second=q.process(job.id,binding,allow,{ClinicalAnnotationOutbox.Response(400,"invalid")},{error("no")})
        assertEquals(ClinicalAnnotationOutbox.State.PAUSED,second.state)
    }
    @Test fun savedReceiptSurvivesLocalAckFailureAndDoesNotUploadAgain()=fixture {
        val q=queue();val job=enqueue(q,binding)
        val pending=q.process(job.id,binding,allow,{receipt(it)},{error("synthetic database failure")})
        assertEquals(ClinicalAnnotationOutbox.State.RECEIPT_SAVED,pending.state);assertNotNull(queue().jobs().single().receiptID)
        clock+=60_000
        val done=queue().process(job.id,binding,allow,{error("must reuse receipt")},{runtime.database.measurementDao().acknowledgeAnnotationIfUnchanged(it)})
        assertTrue(done.localAcknowledged);assertTrue(runtime.database.measurementDao().getById(row.id)!!.annotationSubmitted)
    }
    @Test fun delayedReceiptCannotAcknowledgeNewerRevision()=fixture {
        val q=queue();val job=enqueue(q,binding)
        val newer=row.copy(exudate=2);assertTrue(runtime.database.measurementDao().updateIfUnchanged(row,newer))
        val result=q.process(job.id,binding,allow,{receipt(it)},{runtime.database.measurementDao().acknowledgeAnnotationIfUnchanged(it)})
        assertEquals(ClinicalAnnotationOutbox.State.COMPLETE,result.state);assertFalse(result.localAcknowledged)
        assertEquals(newer,runtime.database.measurementDao().getById(row.id))
    }
    @Test fun permissionLostAfterNetworkKeepsReceiptButDoesNotMarkLocal()=fixture {
        val q=queue();val job=enqueue(q,binding);var count=0
        val result=q.process(job.id,binding,{if(count++==0) ClinicalAnnotationOutbox.Permission.ALLOW else ClinicalAnnotationOutbox.Permission.REVOKED},
            {receipt(it)},{error("must not ack")})
        assertNotNull(result.receiptID);assertEquals(ClinicalAnnotationOutbox.State.CANCELLED,result.state)
    }
    @Test fun cancellationLeavesDurableAttemptForRecovery()=fixture {
        val q=queue();val job=enqueue(q,binding)
        try {q.process(job.id,binding,allow,{throw CancellationException("synthetic cancellation")},{error("no")});fail("cancellation swallowed")}
        catch(_:CancellationException){}
        val recovered=queue().jobs().single();assertEquals(ClinicalAnnotationOutbox.State.RETRY,recovered.state);assertEquals(1,recovered.attempts)
    }
    @Test fun corruptEnvelopeStopsQueueInsteadOfDiscardingPendingWork()=fixture {
        val q=queue();val job=enqueue(q,binding);File(q.directory,"${job.id}.enc").writeBytes(byteArrayOf(0,1,2))
        try {queue().jobs();fail("corrupt queue ignored")}catch(_:Exception){}
        try {enqueue(queue(),binding);fail("writes continued")}catch(_:Exception){}
        assertEquals(1,requireNotNull(q.directory.listFiles()).size)
    }
    @Test fun scopesCannotReadOrSendEachOthersWork()=fixture {
        val q=queue();val job=enqueue(q,binding);val review=queue(MedicalDataScope.SYNTHETIC_REVIEW)
        assertTrue(review.jobs().isEmpty());assertEquals(1,q.jobs().size)
        review.directory.mkdirs();File(q.directory,"${job.id}.enc").copyTo(File(review.directory,"${job.id}.enc"))
        try {review.jobs();fail("cross-scope envelope accepted")}catch(_:IllegalArgumentException){}
    }
    @Test fun slowUploadDoesNotBlockStatusReadOrAnotherEnqueue()=fixture {
        val q=queue();val job=enqueue(q,binding)
        coroutineScope {
            val entered=CompletableDeferred<Unit>();val finish=CompletableDeferred<Unit>()
            val sending=async {q.process(job.id,binding,allow,{entered.complete(Unit);finish.await();receipt(it)},{true})}
            entered.await()
            try {
                withTimeout(1000) {
                    assertEquals(1,queue().jobs().size)
                    enqueue(queue(),binding.copy(actor="another-actor"))
                    assertEquals(2,queue().jobs().size)
                }
            } finally {finish.complete(Unit)}
            sending.await()
        }
    }
    @Test fun simultaneousProcessorsAcrossInstancesOnlySendOnce()=fixture {
        val q=queue();val job=enqueue(q,binding);var sent=0
        coroutineScope {
            List(2){async {queue().process(job.id,binding,allow,{sent++;delay(50);receipt(it)},{true})}}.awaitAll()
        }
        assertEquals(1,sent);assertEquals(ClinicalAnnotationOutbox.State.COMPLETE,q.jobs().single().state)
    }
}
