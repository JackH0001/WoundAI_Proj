package com.woundmeasurement.app.data.store

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.graphics.Bitmap
import androidx.test.core.app.ApplicationProvider
import com.woundmeasurement.app.BuildConfig
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID
import java.util.zip.ZipInputStream

class PrivateExportsTest {
    private class IsolatedContext(base: Context): ContextWrapper(base) {
        val prefix = "exports-${UUID.randomUUID()}"
        override fun getApplicationContext(): Context = this
        override fun getNoBackupFilesDir() = File(super.getNoBackupFilesDir(),prefix).apply { mkdirs() }
        override fun getSharedPreferences(name: String, mode: Int): SharedPreferences = super.getSharedPreferences("$prefix-$name",mode)
        fun clear() {
            noBackupFilesDir.deleteRecursively()
            MedicalDataScope.entries.forEach { super.deleteSharedPreferences("$prefix-${it.settingsName}") }
        }
    }
    private fun fixture(test: (IsolatedContext,PrivateExports,PrivateExports)->Unit) {
        assumeTrue(BuildConfig.MMH_VALIDATION)
        val ctx=IsolatedContext(ApplicationProvider.getApplicationContext())
        try { test(ctx,PrivateExports(ctx,MedicalDataScope.MMH),PrivateExports(ctx,MedicalDataScope.SYNTHETIC_REVIEW)) }
        finally {ctx.clear()}
    }
    @Test fun defaultOffAndPreferencesPersistOnlyInOwnEnvironment() = fixture {ctx,a,b ->
        assertFalse(a.enabled);assertFalse(b.enabled)
        assertNull(a.save(mapOf("capture.jpg" to byteArrayOf(1))))
        assertTrue(a.list().isEmpty())
        a.enabled=true;assertTrue(PrivateExports(ctx,MedicalDataScope.MMH).enabled);assertFalse(b.enabled)
    }
    @Test fun encryptedNoBackupStorageAndExplicitExportHaveMatchingBytesAndHashes() = fixture {ctx,a,b ->
        a.enabled=true
        val payload="synthetic-administrative-image".toByteArray()
        val name=requireNotNull(a.save(mapOf("capture.jpg" to payload)))
        assertTrue(a.directory.canonicalPath.startsWith(ctx.noBackupFilesDir.canonicalPath+"/"))
        assertFalse(File(a.directory,name).readBytes().toString(Charsets.UTF_8).contains("synthetic-administrative-image"))
        assertArrayEquals(payload,a.read(name)["capture.jpg"])
        assertTrue(b.list().isEmpty())
        val out=ByteArrayOutputStream();a.export(name,out)
        val files=mutableMapOf<String,ByteArray>()
        ZipInputStream(out.toByteArray().inputStream()).use { zip ->
            while(true) {val entry=zip.nextEntry?:break;files[entry.name]=zip.readBytes()}
        }
        assertEquals(setOf("capture.jpg","sha256.json"),files.keys)
        assertArrayEquals(payload,files["capture.jpg"])
        val expected=java.security.MessageDigest.getInstance("SHA-256").digest(payload).joinToString(""){"%02x".format(it)}
        assertEquals(expected,org.json.JSONObject(String(files.getValue("sha256.json"))).getString("capture.jpg"))
    }
    @Test fun tamperingCannotExportAnyPlaintext() = fixture {_,a,_ ->
        a.enabled=true;val name=requireNotNull(a.save(mapOf("capture.jpg" to byteArrayOf(1,2,3))))
        val file=File(a.directory,name);val bytes=file.readBytes();bytes[bytes.lastIndex]=(bytes.last().toInt() xor 1).toByte();file.writeBytes(bytes)
        val out=ByteArrayOutputStream()
        assertThrows(Exception::class.java){a.export(name,out)};assertEquals(0,out.size())
    }
    @Test fun movingEncryptedFileBetweenEnvironmentsIsRejected() = fixture {_,a,b ->
        a.enabled=true;b.enabled=true
        val name=requireNotNull(a.save(mapOf("capture.jpg" to byteArrayOf(1,2,3))))
        b.directory.mkdirs();File(a.directory,name).copyTo(File(b.directory,name))
        assertThrows(Exception::class.java){b.read(name)}
    }
    @Test fun offKeepsExistingCopiesButPreventsNewOnes() = fixture {_,a,_ ->
        a.enabled=true;val name=requireNotNull(a.save(mapOf("capture.jpg" to byteArrayOf(4))))
        a.enabled=false;assertNull(a.save(mapOf("capture.jpg" to byteArrayOf(5))))
        assertEquals(listOf(name),a.list());assertArrayEquals(byteArrayOf(4),a.read(name)["capture.jpg"])
    }
    @Test fun pathTraversalAndReservedNamesRejected() = fixture {_,a,_ ->
        a.enabled=true
        assertThrows(Exception::class.java){a.read("../other.enc")}
        assertThrows(Exception::class.java){a.save(mapOf("../capture.jpg" to byteArrayOf(1)))}
        assertThrows(Exception::class.java){a.save(mapOf("sha256.json" to byteArrayOf(1)))}
        assertTrue(a.list().isEmpty())
    }
    @Test fun overlayBundlePreservesPairedAttachmentsAndCannotOverrideMetadata() = fixture {_,a,_ ->
        a.enabled=true
        val image=Bitmap.createBitmap(8,12,Bitmap.Config.ARGB_8888)
        val parts=mapOf("measurement.jpg" to byteArrayOf(9,8),"wound.png" to byteArrayOf(1,0),"tissue.png" to byteArrayOf(5,0))
        try {
            val name=requireNotNull(a.saveBitmap(image,"overlay",org.json.JSONObject().put("image_id","synthetic-image"),parts))
            val saved=a.read(name)
            assertEquals(parts.keys+setOf("overlay.jpg","result.json"),saved.keys)
            parts.forEach {(key,bytes)->assertArrayEquals(bytes,saved[key])}
            assertEquals("synthetic-image",org.json.JSONObject(String(saved.getValue("result.json"))).getString("image_id"))
            assertThrows(Exception::class.java){a.saveBitmap(image,"overlay",attachments=mapOf("result.json" to byteArrayOf(1)))}
        } finally {image.recycle()}
    }
    @Test fun mmhGalleryEntryNeverFallsBackToSharedStorageWhenOff() = fixture {ctx,a,_ ->
        val image=Bitmap.createBitmap(8,12,Bitmap.Config.ARGB_8888)
        try {
            assertNull(GalleryExport.saveCapture(ctx,image,"clinical"))
            assertNull(GalleryExport.saveOverlay(ctx,image,"phantom"))
            a.enabled=true
            assertNull(GalleryExport.saveCapture(ctx,image,"clinical"))
            assertNull(GalleryExport.saveOverlay(ctx,image,"phantom"))
            assertTrue(a.list().isEmpty())
        } finally {image.recycle()}
    }
}
