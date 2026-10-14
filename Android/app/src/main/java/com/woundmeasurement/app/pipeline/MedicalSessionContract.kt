package com.woundmeasurement.app.pipeline

import com.google.gson.JsonParser
import java.io.IOException

/** Strict live receipt, not a decoder for arbitrary cached or legacy login responses. */
object MedicalSessionContract {
    fun decode(raw: String, previous: LoginIdentity?, nowSeconds: Double = System.currentTimeMillis()/1000.0, expectedOrg: String = "mmhps20261007"): LoginIdentity {
        try {
            val j = JsonParser.parseString(raw).asJsonObject
            fun string(key: String): String { val v=j.get(key).asJsonPrimitive; require(v.isString); return v.asString }
            require(string("session_contract") == "account-revision-v1")
            val org=string("org"); val user=string("user"); val actor=string("identity")
            require(org==expectedOrg && user.isNotBlank() && actor=="$org:$user")
            require(previous==null || previous.identity==actor)
            val role=string("role"); require(role in setOf("physician","nurse","assistant","engineer","admin"))
            val permissions=j.getAsJsonArray("perms").map {
                require(it.isJsonPrimitive && it.asJsonPrimitive.isString); it.asString
            }.toSet()
            require("measure.sample" in permissions)
            val exp=j.get("expires_at").asJsonPrimitive; require(exp.isNumber)
            require(exp.asDouble.isFinite() && exp.asDouble > nowSeconds)
            val display=j.get("display_name")?.takeUnless { it.isJsonNull }?.let {
                require(it.isJsonPrimitive && it.asJsonPrimitive.isString); it.asString
            }
            return LoginIdentity(actor,org,user,role,string("role_zh"),display,permissions)
        } catch (_: Exception) { throw IOException("登入狀態回覆不完整或身分不符，未沿用工作階段") }
    }
}
