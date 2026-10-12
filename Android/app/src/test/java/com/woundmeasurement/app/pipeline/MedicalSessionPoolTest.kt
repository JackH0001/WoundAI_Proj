package com.woundmeasurement.app.pipeline

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class MedicalSessionPoolTest {
    private fun identity(role: String = "physician", user: String = "dr01") = LoginIdentity(
        "mmhps20261007:$user","mmhps20261007",user,role,role,null,setOf("measure.sample"))
    private inner class Fake : MedicalSessionClient {
        override var identity: LoginIdentity? = null
        override var hasSessionToken = false
        var logins=0; var checks=0; var failure=0
        var validation: (() -> Unit)? = null
        override fun login(username: String,password: String): Boolean { logins++;hasSessionToken=true;return true }
        override fun validateMedicalSession(): LoginIdentity {
            checks++; identity=null;validation?.invoke()
            if (failure!=0) { if (failure==401) hasSessionToken=false;throw IOException("test failure") }
            return identity().also { identity=it }
        }
    }
    @Volatile private var epoch=0L
    @Volatile private var origin="https://synthetic.invalid"
    private fun input()=MedicalSessionPool.Input(origin,"dr01",java.util.UUID.randomUUID().toString(),epoch)
    private fun rejects(block: () -> Unit) { try { block();fail("Expected rejection") } catch (_: IOException) {} }
    @Test fun tenAcquisitionsOneLoginTenChecks() {
        val fake=Fake();val pool=MedicalSessionPool(::input) { fake }
        repeat(10) { assertSame(fake,pool.acquire()) }
        assertEquals(1,fake.logins);assertEquals(10,fake.checks)
    }
    @Test fun concurrentCallersShareCheck() {
        val fake=Fake();val entered=CountDownLatch(1);val release=CountDownLatch(1)
        fake.validation={ entered.countDown();check(release.await(3,TimeUnit.SECONDS)) }
        val reads=AtomicInteger();val secondRead=CountDownLatch(1)
        val secondThread=java.util.concurrent.atomic.AtomicReference<Thread>()
        val pool=MedicalSessionPool(input={if(reads.incrementAndGet()==2)secondRead.countDown();input()}) { fake }
        val threads=Executors.newFixedThreadPool(2)
        try {
            val a=threads.submit<Fake> { pool.acquire() };assertTrue(entered.await(3,TimeUnit.SECONDS))
            val b=threads.submit<Fake> { secondThread.set(Thread.currentThread());pool.acquire() };assertTrue(secondRead.await(3,TimeUnit.SECONDS))
            // Wait until the second future is blocked in acquire, not merely scheduled.
            val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(3)
            while (secondThread.get().state!=Thread.State.WAITING && System.nanoTime()<deadline) Thread.yield()
            assertEquals(Thread.State.WAITING,secondThread.get().state);release.countDown()
            assertSame(a.get(3,TimeUnit.SECONDS),b.get(3,TimeUnit.SECONDS))
            assertEquals(1,fake.logins);assertEquals(1,fake.checks)
        } finally { release.countDown();threads.shutdownNow() }
    }
    @Test fun changedCredentialsOrOriginCreatesFreshClient() {
        val pool=MedicalSessionPool(::input) { Fake() }
        val a=pool.acquire();epoch++;val b=pool.acquire();origin="https://second.invalid"
        assertNotSame(a,b);assertNotSame(b,pool.acquire())
    }
    @Test fun accountChangeDuringValidationRejectsLateResult() {
        val fake=Fake();fake.validation={epoch++};val pool=MedicalSessionPool(::input) { fake }
        rejects { pool.acquire() }
    }
    @Test fun temporaryFailureDoesNotRepeatPasswordLogin() {
        val fake=Fake();val pool=MedicalSessionPool(::input) { fake };pool.acquire();fake.failure=503
        rejects { pool.acquire() };assertNull(fake.identity);assertEquals(1,fake.logins)
        fake.failure=0;pool.acquire();assertEquals(1,fake.logins)
    }
    @Test fun revokedTokenRequiresLaterExplicitAcquisition() {
        val fake=Fake();val pool=MedicalSessionPool(::input) { fake };pool.acquire();fake.failure=401
        rejects { pool.acquire() };assertEquals(1,fake.logins)
        fake.failure=0;pool.acquire();assertEquals(2,fake.logins)
    }
    @Test fun wrongReturnedAccountRejected() {
        val fake=object: MedicalSessionClient {
            override val identity=identity(user="other")
            override val hasSessionToken=true
            override fun login(username:String,password:String)=true
            override fun validateMedicalSession()=identity
        }
        rejects { MedicalSessionPool(::input) { fake }.acquire() }
    }
    @Test fun missingCredentialsNeverConstructClient() {
        val pool=MedicalSessionPool<Fake>(input={MedicalSessionPool.Input(origin,"dr01","",epoch)}) { error("must not construct") }
        rejects { pool.acquire() }
    }
    @Test fun lateOldAccountResultCannotReplaceNewCache() {
        val old=Fake();val next=Fake();var made=0
        val entered=CountDownLatch(1);val release=CountDownLatch(1)
        old.validation={entered.countDown();check(release.await(3,TimeUnit.SECONDS))}
        val pool=MedicalSessionPool(::input) { if(made++==0)old else next }
        val thread=Executors.newSingleThreadExecutor()
        try {
            val first=thread.submit<Fake> { pool.acquire() };assertTrue(entered.await(3,TimeUnit.SECONDS))
            epoch++;assertSame(next,pool.acquire());release.countDown()
            try {first.get(3,TimeUnit.SECONDS);fail("Old result accepted")}catch (e:java.util.concurrent.ExecutionException) {assertTrue(e.cause is IOException)}
            assertSame(next,pool.acquire());assertEquals(2,made);assertEquals(1,next.logins)
        } finally {release.countDown();thread.shutdownNow()}
    }
    @Test fun epochInvalidationAlwaysAdvances() {
        val before=MedicalSessionEpoch.snapshot();MedicalSessionEpoch.invalidate();assertTrue(MedicalSessionEpoch.snapshot()>before)
    }
}
