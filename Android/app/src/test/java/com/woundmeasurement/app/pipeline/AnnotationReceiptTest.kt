package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test

class AnnotationReceiptTest {
    private val valid = """{"status":"enqueued","code":"WD-test","image_id":"image-test","annotation_receipt_id":"0123456789abcdef"}"""
    private fun accepted(body: String = valid, status: Int = 200) =
        AnnotationReceipt.validate(status, body, "WD-test", "image-test").first
    @Test fun storedReceiptAccepted() { assertTrue(accepted()) }
    @Test fun duplicateAcceptedWithoutCreatingNewRecord() {
        val r = AnnotationReceipt.validate(200, valid.replace("enqueued", "duplicate_skipped"), "WD-test", "image-test")
        assertTrue(r.first); assertTrue(r.second.startsWith("duplicate:"))
    }
    @Test fun other2xxAreNotPersistenceEvidence() { for (s in listOf(201,202,204,206)) assertFalse(accepted(status=s)) }
    @Test fun redirectsAndErrorsRejectedEvenWithReceipt() { for (s in listOf(302,401,403,409,429,500,503)) assertFalse(accepted(status=s)) }
    @Test fun htmlLoginRejected() { assertFalse(accepted("<html>Sign in</html>")) }
    @Test fun malformedJsonRejected() { assertFalse(accepted("{broken")) }
    @Test fun emptyRejected() { assertFalse(accepted("")) }
    @Test fun arrayRejected() { assertFalse(accepted("[$valid]")) }
    @Test fun scalarRejected() { assertFalse(accepted("true")) }
    @Test fun wrongCaseRejected() { assertFalse(accepted(valid.replace("WD-test","WD-other"))) }
    @Test fun wrongImageRejected() { assertFalse(accepted(valid.replace("image-test","other"))) }
    @Test fun missingImageRejected() { assertFalse(accepted(valid.replace("\"image_id\":\"image-test\",", ""))) }
    @Test fun missingReceiptRejected() { assertFalse(accepted(valid.replace(",\"annotation_receipt_id\":\"0123456789abcdef\"", ""))) }
    @Test fun missingCaseRejected() { assertFalse(accepted(valid.replace("\"code\":\"WD-test\",", ""))) }
    @Test fun legacyDuplicateWithoutBindingsRejected() { assertFalse(accepted("""{"status":"duplicate_skipped","code":"WD-test"}""")) }
    @Test fun badReceiptRejected() { for (v in listOf("", " ", "0123456789abcde", "0123456789abcdeff", "0123456789abcdeg", "0123456789ABCDEF")) assertFalse(accepted(valid.replace("0123456789abcdef",v))) }
    @Test fun receiptMustBeString() { assertFalse(accepted(valid.replace("\"0123456789abcdef\"", "1234567890123456"))) }
    @Test fun nullReceiptRejected() { assertFalse(accepted(valid.replace("\"0123456789abcdef\"", "null"))) }
    @Test fun unexpectedStatusRejected() { for (v in listOf("ok","saved","pending","UNKNOWN","UNKNOWN_CONDITIONAL","ENQUEUED")) assertFalse(accepted(valid.replace("enqueued",v))) }
    @Test fun errorEvenAlongsideSuccessRejected() { assertFalse(accepted(valid.dropLast(1)+",\"error\":\"write_failed\"}")) }
    @Test fun issuesEvenAlongsideSuccessRejected() { assertFalse(accepted(valid.dropLast(1)+",\"issues\":[]}")) }
    @Test fun duplicateWrongImageRejected() { assertFalse(accepted(valid.replace("enqueued","duplicate_skipped").replace("image-test","other"))) }
    @Test fun serverNoteCannotSpoofDuplicateResult() {
        val r=AnnotationReceipt.validate(200,valid.dropLast(1)+",\"note\":\"duplicate\"}","WD-test","image-test")
        assertTrue(r.first); assertFalse(r.second.contains("duplicate"))
    }
}
