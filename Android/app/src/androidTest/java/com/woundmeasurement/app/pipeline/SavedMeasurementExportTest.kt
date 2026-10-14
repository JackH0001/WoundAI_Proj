package com.woundmeasurement.app.pipeline

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.graphics.Bitmap
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.store.*
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.Date
import java.util.UUID

class SavedMeasurementExportTest {
    private class TestContext(base:Context):ContextWrapper(base) {
        val root=File(base.cacheDir,"saved-export-${UUID.randomUUID()}").apply{mkdirs()}
        override fun getApplicationContext():Context=this
        override fun getFilesDir()=File(root,"files").apply{mkdirs()}
        override fun getNoBackupFilesDir()=File(root,"no-backup").apply{mkdirs()}
        override fun getDatabasePath(name:String)=File(root,name)
        override fun getSharedPreferences(name:String,mode:Int):SharedPreferences=super.getSharedPreferences(root.name+name,mode)
        fun clear(){ MedicalDataScope.entries.forEach{super.deleteSharedPreferences(root.name+it.settingsName)};root.deleteRecursively() }
    }
    private fun fixture(block:suspend (MedicalRuntime,PrivateExports)->Unit)=runBlocking {
        val ctx=TestContext(ApplicationProvider.getApplicationContext());val runtime=MedicalRuntime(ctx)
        val exports=PrivateExports(ctx,MedicalDataScope.MMH);exports.enabled=true
        try{block(runtime,exports)}finally{runtime.deactivate();runtime.database.close();ctx.clear()}
    }
    private suspend fun row(runtime:MedicalRuntime):MeasurementEntity {
        val bitmap=Bitmap.createBitmap(64,96,Bitmap.Config.ARGB_8888).apply{eraseColor(0xff996644.toInt())}
        val image=try{requireNotNull(runtime.images.save(bitmap))}finally{bitmap.recycle()}
        val mask=ByteArray(64){if(it==18)0 else 1}
        val raster=EditRaster(mask,ByteArray(64){1},mask.copyOf(),4f,6f,8,8,1f,.1,
            maskPx=63,canvasW=64,canvasH=96,autoBaseline=ByteArray(64){1})
        val (bytes,meta)=requireNotNull(EditRasterCodec.encode(raster))
        val value=MeasurementEntity(patientId=null,timestamp=Date(1000),hasWound=true,confidence=1.0,
            estimatedArea=6.3,estimatedVolume=null,woundType=null,quality="synthetic",processingTime=0,
            imagePath=image,dataPath="",imageId="synthetic-image",source="phantom",imageW=64,imageH=96,
            gtPolygon="[[4,6],[12,6],[12,14],[4,14]]",doctorVerified=true,exudate=1,
            tissueGranulation=1.0,tissueSlough=0.0,tissueNecrosis=0.0,tissueEpithelial=0.0,tissueOther=0.0,
            rasterPath=requireNotNull(runtime.images.save(bytes)),rasterMeta=meta)
        return value.copy(id=runtime.database.measurementDao().insertMeasurement(value))
    }
    @Test fun sameSavedRevisionIsDeduplicatedAndUsesExactImageBytes()=fixture{runtime,exports->
        val row=row(runtime);val writer=SavedMeasurementExport(runtime,exports)
        val first=requireNotNull(writer.save(row));assertEquals(first,writer.save(row));assertEquals(1,exports.list().size)
        val files=exports.read(first)
        assertArrayEquals(runtime.images.rawBytes(row.imagePath),files["measurement.jpg"])
        assertEquals(setOf("measurement.jpg","overlay.jpg","result.json","wound.png","tissue.png"),files.keys)
        val json=JSONObject(String(files.getValue("result.json")))
        assertEquals(row.id,json.getLong("local_record_id"));assertEquals(row.timestamp.time,json.getLong("record_created_at_ms"))
        assertTrue(json.isNull("captured_at"));assertEquals(6.3,json.getDouble("area_cm2"),0.0)
    }
    @Test fun revisionKeepsPreviousAssetsAndChangesResult()=fixture{runtime,exports->
        val old=row(runtime);val writer=SavedMeasurementExport(runtime,exports)
        val name=requireNotNull(writer.save(old));val original=exports.read(name)
        val new=old.copy(exudate=2,estimatedArea=7.0)
        assertTrue(runtime.database.measurementDao().updateIfUnchanged(old,new))
        val next=requireNotNull(writer.save(new));assertNotEquals(name,next);assertEquals(2,exports.list().size)
        original.forEach{(key,bytes)->assertArrayEquals(bytes,exports.read(name)[key])}
        assertEquals(7.0,JSONObject(String(exports.read(next).getValue("result.json"))).getDouble("area_cm2"),0.0)
    }
    @Test fun uncommittedOrStaleRowCannotProduceCopy()=fixture{runtime,exports->
        val saved=row(runtime);val writer=SavedMeasurementExport(runtime,exports)
        try{writer.save(saved.copy(id=9999));fail("Uncommitted exported")}catch(_:IllegalStateException){}
        val changed=saved.copy(exudate=3);assertTrue(runtime.database.measurementDao().updateIfUnchanged(saved,changed))
        try{writer.save(saved);fail("Stale exported")}catch(_:IllegalStateException){}
        assertTrue(exports.list().isEmpty())
    }
    @Test fun clinicalWithoutCareConsentProducesNoCopy()=fixture{runtime,exports->
        val saved=row(runtime);val clinical=saved.copy(source="clinical")
        assertTrue(runtime.database.measurementDao().updateIfUnchanged(saved,clinical))
        try{SavedMeasurementExport(runtime,exports).save(clinical);fail("Missing consent accepted")}catch(_:IllegalStateException){}
        assertTrue(exports.list().isEmpty())
    }
    @Test fun corruptRasterOrWrongImageGridFailsClosed()=fixture{runtime,exports->
        val saved=row(runtime);val image=requireNotNull(runtime.images.rawBytes(saved.imagePath))
        assertThrows(Exception::class.java){SavedMeasurementExport.build(saved,image,byteArrayOf(0,1))}
        assertThrows(Exception::class.java){SavedMeasurementExport.build(saved.copy(imageW=96),image,runtime.images.rawBytes(saved.rasterPath))}
        assertTrue(exports.list().isEmpty())
    }
    @Test fun closedContextCannotGenerateCopy()=fixture{runtime,exports->
        val saved=row(runtime);runtime.deactivate()
        try{SavedMeasurementExport(runtime,exports).save(saved);fail("Closed exported")}catch(_:IllegalStateException){}
        assertTrue(exports.list().isEmpty())
    }
    @Test fun confirmedRowWithMissingRasterDoesNotSilentlyExport()=fixture{runtime,exports->
        val saved=row(runtime);val missing=saved.copy(rasterPath=null,rasterMeta=null)
        assertTrue(runtime.database.measurementDao().updateIfUnchanged(saved,missing))
        try{SavedMeasurementExport(runtime,exports).save(missing);fail("Missing confirmed mask exported")}catch(_:IllegalArgumentException){}
        assertTrue(exports.list().isEmpty())
    }
    @Suppress("UNCHECKED_CAST")
    private fun viewModel():MeasureViewModel {
        val ctx=ApplicationProvider.getApplicationContext<Context>()
        val vm=MeasureViewModel(WoundAnalyzer(com.woundmeasurement.app.processing.OnnxSegmentationModule(ctx)))
        // Inject only an already-calculated synthetic result; no inference or cloud request.
        val field=MeasureViewModel::class.java.getDeclaredField("_state").apply{isAccessible=true}
        val state=field.get(vm) as kotlinx.coroutines.flow.MutableStateFlow<MeasureUiState>
        state.value=MeasureUiState(result=MeasureResult(5.0,mapOf("granulation" to 1.0),PushScore(8,2,0,10,10),"synthetic",1.0))
        return vm
    }
    @Test fun callbackSeesCommittedRowAndRunsOnce()=fixture{runtime,_ ->
        val vm=viewModel();var called=0
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){
            vm.saveToTimeline(runtime.database.measurementDao(),0,source="phantom",onSaved={ saved ->
                assertEquals(saved,runtime.database.measurementDao().getById(saved.id));called++;"副本完成"
            })
            vm.saveToTimeline(runtime.database.measurementDao(),0,source="phantom",onSaved={called++;null})
        }
        kotlinx.coroutines.withTimeout(5000){vm.state.first{!it.saving}}
        assertEquals(1,called);assertTrue(vm.state.value.saved)
    }
    @Test fun failedDatabaseSaveDoesNotRunCallback()=fixture{runtime,_ ->
        val vm=viewModel();var called=false
        val dao=object:com.woundmeasurement.app.data.dao.MeasurementDao by runtime.database.measurementDao(){
            override suspend fun insertMeasurement(measurement:MeasurementEntity):Long {error("synthetic disk failure")}
        }
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.saveToTimeline(dao,0,source="phantom",onSaved={called=true;null})}
        kotlinx.coroutines.withTimeout(5000){vm.state.first{!it.saving}}
        assertFalse(called);assertFalse(vm.state.value.saved);assertTrue(runtime.database.measurementDao().getAllMeasurementsOnce().isEmpty())
    }
    @Test fun failedOptionalCopyDoesNotMisreportDatabaseSaveAsFailed()=fixture{runtime,_ ->
        val vm=viewModel()
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.saveToTimeline(runtime.database.measurementDao(),0,source="phantom",onSaved={error("synthetic export failure")})}
        kotlinx.coroutines.withTimeout(5000){vm.state.first{!it.saving}}
        assertTrue(vm.state.value.saved);assertEquals(1,runtime.database.measurementDao().getAllMeasurementsOnce().size)
        assertTrue(vm.state.value.submitStatus.orEmpty().contains("行政副本未完成"))
    }
    @Test fun backgroundReceiptReconcilesSavedViewModelWithoutAnotherSubmit()=fixture{runtime,_ ->
        val vm=viewModel();val dao=runtime.database.measurementDao()
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.saveToTimeline(dao,0,source="phantom")}
        kotlinx.coroutines.withTimeout(5000){vm.state.first{!it.saving}}
        val saved=dao.getAllMeasurementsOnce().single();assertTrue(dao.acknowledgeAnnotationIfUnchanged(saved))
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.reconcileSavedAcknowledgement(dao)}
        assertTrue(vm.state.value.annotationSaved)
    }
    @Test fun backgroundReceiptReconciliationDoesNotMarkDifferentLocalRevision()=fixture{runtime,_ ->
        val vm=viewModel();val dao=runtime.database.measurementDao()
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.saveToTimeline(dao,0,source="phantom")}
        kotlinx.coroutines.withTimeout(5000){vm.state.first{!it.saving}}
        val saved=dao.getAllMeasurementsOnce().single();val newer=saved.copy(exudate=2,annotationSubmitted=true)
        assertTrue(dao.updateIfUnchanged(saved,newer))
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main){vm.reconcileSavedAcknowledgement(dao)}
        assertFalse(vm.state.value.annotationSaved)
    }
    @Test fun acknowledgementFlagDoesNotCreateExtraAdministrativeRevision()=fixture{runtime,exports->
        val saved=row(runtime);val writer=SavedMeasurementExport(runtime,exports);val name=writer.save(saved)
        val acknowledged=saved.copy(annotationSubmitted=true)
        assertTrue(runtime.database.measurementDao().updateIfUnchanged(saved,acknowledged))
        assertEquals(name,writer.save(acknowledged));assertEquals(1,exports.list().size)
    }
}
