package com.woundmeasurement.app.data

import com.google.gson.JsonParser
import java.io.File
import org.junit.Assert.*
import org.junit.Test

class MedicalWoundProfileTest {
    private fun cases() = sequenceOf(File("../.."),File(".."),File("."))
        .map{File(it,"engineering/contracts/medical_wound_profile_cases.json")}.first{it.isFile}
        .let { JsonParser.parseString(it.readText()).asJsonArray }
    @Test fun sharedGoldenCasesAndRoundTrip() {
        for (element in cases()) {
            val c=element.asJsonObject
            val p=MedicalWoundProfile.decode(c["profile"].toString())
            assertEquals(c["name"].asString,c["valid"].asBoolean,p!=null)
            if(p!=null)assertEquals(p,MedicalWoundProfile.decode(p.encoded()))
        }
    }
    @Test fun doesNotInferEtiologyOrTotalFromDiabetes() {
        val p=MedicalWoundProfile(site="heel",side="right",surface="plantar",etiology="pressure",factors=listOf("diabetes"))
        assertEquals(listOf("pressure_stage","push"),MedicalCatalog.shared.guidance(p.etiology).map{it.code})
        assertTrue(MedicalCatalog.shared.guidance("unknown").isEmpty())
        assertTrue(MedicalCatalog.shared.instruments.all{it.status=="requirements_only"})
    }
    @Test fun invalidSelectionsCannotBeEncoded() {
        assertFalse(MedicalWoundProfile().valid)
        try { MedicalWoundProfile().encoded();fail("incomplete profile accepted") }catch(_:IllegalArgumentException){}
    }
}
