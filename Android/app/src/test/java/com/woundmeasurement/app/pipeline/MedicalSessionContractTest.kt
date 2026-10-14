package com.woundmeasurement.app.pipeline

import com.google.gson.JsonParser
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

class MedicalSessionContractTest {
    private fun body() = JsonParser.parseString("""{"session_contract":"account-revision-v1","identity":"mmhps20261007:dr01","org":"mmhps20261007","user":"dr01","role":"physician","role_zh":"醫師","perms":["measure.sample","gt.verify"],"expires_at":2000}""").asJsonObject
    private fun rejects(change: (com.google.gson.JsonObject) -> Unit) {
        val j=body();change(j)
        try {MedicalSessionContract.decode(j.toString(),null,1000.0);fail("Invalid receipt accepted")}
        catch (_: IOException) {}
    }
    @Test fun acceptsValidCurrentIdentity() {val me=MedicalSessionContract.decode(body().toString(),null,1000.0);assertEquals("dr01",me.user);assertTrue(me.can("gt.verify"))}
    @Test fun missingContractRejected()=rejects {it.remove("session_contract")}
    @Test fun legacyContractRejected()=rejects {it.addProperty("session_contract","legacy")}
    @Test fun wrongOrganizationRejected()=rejects {it.addProperty("org","other")}
    @Test fun inconsistentActorRejected()=rejects {it.addProperty("identity","mmhps20261007:other")}
    @Test fun stringExpiryRejected()=rejects {it.addProperty("expires_at","2000")}
    @Test fun expiredRejected()=rejects {it.addProperty("expires_at",999)}
    @Test fun missingPermissionRejected()=rejects {it.add("perms",JsonParser.parseString("[]"))}
    @Test fun numericPermissionRejected()=rejects {it.add("perms",JsonParser.parseString("[1,\"measure.sample\"]"))}
    @Test fun unknownRoleRejected()=rejects {it.addProperty("role","owner")}
    @Test fun malformedDisplayNameRejected()=rejects {it.addProperty("display_name",42)}
    @Test fun otherPriorIdentityRejected() {
        val prior=LoginIdentity("mmhps20261007:other","mmhps20261007","other","physician","醫師",null,setOf("measure.sample"))
        try {MedicalSessionContract.decode(body().toString(),prior,1000.0);fail("Identity switch accepted")}catch (_:IOException){}
    }
}
