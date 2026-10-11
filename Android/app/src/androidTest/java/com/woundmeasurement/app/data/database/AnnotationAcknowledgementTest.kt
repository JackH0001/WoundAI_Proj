package com.woundmeasurement.app.data.database

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.entity.MeasurementEntity
import java.util.Date
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith

/** Real Room transactions, isolated in memory: never opens the installed clinical database. */
@RunWith(AndroidJUnit4::class)
class AnnotationAcknowledgementTest {
    private lateinit var db: WoundMeasurementDatabase
    private lateinit var dao: MeasurementDao

    @Before fun open() {
        db = Room.inMemoryDatabaseBuilder(
            InstrumentationRegistry.getInstrumentation().targetContext,
            WoundMeasurementDatabase::class.java).build()
        dao = db.measurementDao()
    }
    @After fun close() { db.close() }

    private suspend fun seed(): MeasurementEntity {
        val row = MeasurementEntity(patientId = null, timestamp = Date(123456L),
            hasWound = true, confidence = 1.0, estimatedArea = 10.0,
            estimatedVolume = null, woundType = null, quality = "synthetic", processingTime = 20,
            imagePath = "synthetic-image", dataPath = "", imageId = "synthetic-image-id",
            source = "phantom", gtPolygon = "[[1,1],[4,1],[1,4]]", imageW = 8, imageH = 8,
            doctorVerified = true, tissueGranulation = 1.0,
            rasterPath = "synthetic-raster", rasterMeta = "synthetic-meta",
            annotationProvenance = "synthetic-provenance")
        return row.copy(id = dao.insertMeasurement(row))
    }

    @Test fun initialUploadAcknowledgesTimelineAndRepeatSkipsNetwork() = runBlocking {
        val sent = seed()
        var calls = 0
        suspend fun send() = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { true }, { calls++; true to "saved" })
        assertTrue(send().acknowledged)
        assertTrue(dao.getById(sent.id)!!.annotationSubmitted)
        assertTrue(send().acknowledged)
        assertEquals(1, calls)
        assertEquals(1, dao.getAllMeasurementsOnce().size)
    }
    @Test fun initialUploadRejectsUnsavedRasterAndStaleRowBeforeNetwork() = runBlocking {
        val sent = seed()
        var calls = 0
        val mismatch = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { false }, { calls++; true to "saved" })
        assertFalse(mismatch.acknowledged)
        dao.updateMeasurement(sent.copy(exudate = 3))
        val stale = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { true }, { calls++; true to "saved" })
        assertFalse(stale.acknowledged)
        assertEquals(0, calls)
    }
    @Test fun cloudSavedButLocalWriteFailsIsNotReportedAsUploadFailure() = runBlocking {
        val sent = seed()
        val failing = object : MeasurementDao by dao {
            override suspend fun acknowledgeAnnotationIfUnchanged(expected: MeasurementEntity): Boolean {
                throw IllegalStateException("synthetic storage failure")
            }
        }
        val outcome = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            failing, sent, { true }, { true to "saved" })
        assertFalse(outcome.acknowledged)
        assertTrue(outcome.message.startsWith("標註已保存"))
        assertTrue(outcome.message.contains("不必重新拍攝"))
        assertFalse(dao.getById(sent.id)!!.annotationSubmitted)
    }
    @Test fun failedServerReceiptLeavesInitialRevisionPending() = runBlocking {
        val sent = seed()
        val outcome = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { true }, { false to "invalid receipt" })
        assertFalse(outcome.acknowledged)
        assertFalse(dao.getById(sent.id)!!.annotationSubmitted)
    }
    @Test fun revisionChangedDuringNetworkIsNotAcknowledged() = runBlocking {
        val sent = seed()
        val revised = sent.copy(estimatedArea = 20.0)
        val outcome = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { true }, { dao.updateMeasurement(revised); true to "saved" })
        assertFalse(outcome.acknowledged)
        assertEquals(revised, dao.getById(sent.id))
    }
    @Test fun duplicateReceiptAcknowledgesExistingRow() = runBlocking {
        val sent = seed()
        val outcome = com.woundmeasurement.app.pipeline.SavedAnnotationUpload.submit(
            dao, sent, { true }, { true to "duplicate:receipt" })
        assertTrue(outcome.acknowledged)
        assertTrue(outcome.message.contains("去重"))
        assertTrue(dao.getById(sent.id)!!.annotationSubmitted)
    }
    @Test fun resaveIdenticalContentKeepsReceiptAndOriginalTime() = runBlocking {
        val old = seed().copy(annotationSubmitted = true)
        val proposed = old.copy(imagePath = "new-encrypted-image", rasterPath = "new-encrypted-raster",
            timestamp = Date(999999), notes = "reformatted summary")
        val result = com.woundmeasurement.app.pipeline.MeasurementRevision.forResave(old, proposed, true, true)
        assertTrue(result.annotationSubmitted)
        assertEquals(old.timestamp, result.timestamp)
        assertEquals(proposed.imagePath, result.imagePath)
    }
    @Test fun resaveChangedPixelsOrFieldsClearsOldReceipt() = runBlocking {
        val old = seed().copy(annotationSubmitted = true)
        val policy = com.woundmeasurement.app.pipeline.MeasurementRevision
        assertFalse(policy.forResave(old, old, false, true).annotationSubmitted)
        assertFalse(policy.forResave(old, old, true, false).annotationSubmitted)
        for (changed in listOf(old.copy(estimatedArea = 12.0), old.copy(exudate = 3),
            old.copy(imageId = "different-image"), old.copy(annotationProvenance = "new-model"),
            old.copy(tissueGranulation = 0.4, tissueSlough = 0.6), old.copy(doctorVerified = false))) {
            assertFalse(policy.forResave(old, changed, true, true).annotationSubmitted)
        }
    }
    @Test fun resaveCannotInventReceiptForUnsentRecord() = runBlocking {
        val old = seed()
        assertFalse(com.woundmeasurement.app.pipeline.MeasurementRevision.forResave(
            old, old.copy(annotationSubmitted = true), true, true).annotationSubmitted)
    }

    @Test fun exactAcknowledgementAndRepeatKeepOneRecord() = runBlocking {
        val sent = seed()
        assertTrue(dao.acknowledgeAnnotationIfUnchanged(sent))
        assertTrue(dao.acknowledgeAnnotationIfUnchanged(sent))
        assertEquals(sent.copy(annotationSubmitted = true), dao.getById(sent.id))
        assertEquals(1, dao.getAllMeasurementsOnce().size)
    }
    @Test fun delayedReplyCannotMarkChangedMaskEvenWithSameArea() = runBlocking {
        val sent = seed()
        val revised = sent.copy(gtPolygon = "[[1,1],[5,1],[1,4]]", rasterPath = "new-raster")
        dao.updateMeasurement(revised)
        assertFalse(dao.acknowledgeAnnotationIfUnchanged(sent))
        assertEquals(revised, dao.getById(sent.id))
    }
    @Test fun tissueOnlyRevisionRejectsOldReply() = runBlocking {
        val sent = seed()
        val revised = sent.copy(tissueGranulation = 0.5, tissueSlough = 0.5)
        dao.updateMeasurement(revised)
        assertFalse(dao.acknowledgeAnnotationIfUnchanged(sent))
        assertEquals(revised, dao.getById(sent.id))
    }
    @Test fun changedImageOrProvenanceRejectsOldReply() = runBlocking {
        val sent = seed()
        for (revised in listOf(sent.copy(imageId = "different-image"),
            sent.copy(annotationProvenance = "different-provenance"),
            sent.copy(exudate = 2), sent.copy(doctorVerified = false))) {
            dao.updateMeasurement(revised)
            assertFalse(dao.acknowledgeAnnotationIfUnchanged(sent))
            assertEquals(revised, dao.getById(sent.id))
        }
    }
    @Test fun deletedRecordIsNeverResurrected() = runBlocking {
        val sent = seed()
        dao.deleteById(sent.id)
        assertFalse(dao.acknowledgeAnnotationIfUnchanged(sent))
        assertFalse(dao.updateIfUnchanged(sent, sent.copy(estimatedArea = 11.0)))
        assertNull(dao.getById(sent.id))
    }
    @Test fun staleEditorCannotEraseSuccessfulAcknowledgement() = runBlocking {
        val editing = seed()
        assertTrue(dao.acknowledgeAnnotationIfUnchanged(editing))
        assertFalse(dao.updateIfUnchanged(editing, editing.copy(estimatedArea = 11.0)))
        assertEquals(editing.copy(annotationSubmitted = true), dao.getById(editing.id))
    }
    @Test fun currentRevisionEditClearsReceiptWithoutChangingCaptureTime() = runBlocking {
        val sent = seed()
        assertTrue(dao.acknowledgeAnnotationIfUnchanged(sent))
        val current = dao.getById(sent.id)!!
        val revised = current.copy(estimatedArea = 11.0, annotationSubmitted = false)
        assertTrue(dao.updateIfUnchanged(current, revised))
        assertEquals(revised, dao.getById(sent.id))
        assertEquals(123456L, dao.getById(sent.id)!!.timestamp.time)
        assertFalse(dao.acknowledgeAnnotationIfUnchanged(sent))
    }
    @Test fun staleEditorCannotOverwriteAnotherRevisionOrAnotherRecord() = runBlocking {
        val before = seed()
        val revised = before.copy(estimatedArea = 12.0)
        assertTrue(dao.updateIfUnchanged(before, revised))
        assertFalse(dao.updateIfUnchanged(before, before.copy(estimatedArea = 11.0)))
        assertFalse(dao.updateIfUnchanged(revised, revised.copy(id = revised.id + 1)))
        assertEquals(revised, dao.getById(before.id))
    }
}
