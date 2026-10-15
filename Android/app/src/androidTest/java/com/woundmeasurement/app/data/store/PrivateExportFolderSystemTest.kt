package com.woundmeasurement.app.data.store

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import android.view.accessibility.AccessibilityNodeInfo
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.core.app.ApplicationProvider
import androidx.test.platform.app.InstrumentationRegistry
import com.woundmeasurement.app.pipeline.MedicalRuntime
import com.woundmeasurement.app.pipeline.PrivateExportSettings
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.FixMethodOrder
import org.junit.Rule
import org.junit.Test
import org.junit.runners.MethodSorters
import java.io.File

/** System DocumentsUI integration on the English reference emulator.
 * Run a and b in separate instrumentation invocations with force-stop between them
 * to prove persisted grants survive an actual app-process restart.
 * Only uniquely named synthetic files produced by this test are removed.
 */
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class PrivateExportFolderSystemTest {
    @get:Rule val compose=createComposeRule()
    private val context get()=ApplicationProvider.getApplicationContext<Context>()
    private val state get()=File(context.noBackupFilesDir,"folder-system-test.json")
    private val store get()=PrivateExports(context,MedicalDataScope.SYNTHETIC_REVIEW)
    private val ui get()=InstrumentationRegistry.getInstrumentation().uiAutomation
    private fun find(predicate:(AccessibilityNodeInfo)->Boolean):AccessibilityNodeInfo? {
        fun walk(node:AccessibilityNodeInfo?):AccessibilityNodeInfo? {
            node ?: return null
            if(predicate(node)) return node
            for(i in 0 until node.childCount) walk(node.getChild(i))?.let{return it}
            return null
        }
        return walk(ui.rootInActiveWindow)
    }
    private fun awaitNode(predicate:(AccessibilityNodeInfo)->Boolean):AccessibilityNodeInfo {
        val deadline=android.os.SystemClock.uptimeMillis()+8000
        while(android.os.SystemClock.uptimeMillis()<deadline) {
            find(predicate)?.let{return it}
            android.os.SystemClock.sleep(100)
        }
        error("System picker control unavailable")
    }
    private fun click(predicate:(AccessibilityNodeInfo)->Boolean) {
        var node=awaitNode(predicate)
        while(!node.isClickable) node=requireNotNull(node.parent)
        check(node.performAction(AccessibilityNodeInfo.ACTION_CLICK))
    }
    private fun children(tree:Uri):List<Uri> {
        val id=DocumentsContract.getTreeDocumentId(tree)
        val child=DocumentsContract.buildChildDocumentsUriUsingTree(tree,id)
        return context.contentResolver.query(child,arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID),null,null,null)!!.use{c->
            buildList{while(c.moveToNext())add(DocumentsContract.buildDocumentUriUsingTree(tree,c.getString(0)))}
        }
    }
    private fun readChildren(parent:Uri):Map<String,ByteArray> {
        val child=DocumentsContract.buildChildDocumentsUriUsingTree(parent,DocumentsContract.getDocumentId(parent))
        return context.contentResolver.query(child,arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID,DocumentsContract.Document.COLUMN_DISPLAY_NAME),null,null,null)!!.use{c->
            buildMap{while(c.moveToNext()) {
                val uri=DocumentsContract.buildDocumentUriUsingTree(parent,c.getString(0))
                put(c.getString(1),context.contentResolver.openInputStream(uri)!!.use{it.readBytes()})
            }}
        }
    }
    @Test fun a_selectFolderAndDeliverVerifiedBytes() {
        assertFalse("Finish/clean the previous synthetic folder test before starting",state.exists())
        val originalFolder=store.outputFolder;val originalEnabled=store.enabled
        assertNull("Reference emulator must start with no custom folder", originalFolder)
        store.usePrivateFolder();store.enabled=true
        compose.setContent{MaterialTheme{Column{PrivateExportSettings(MedicalRuntime(context,MedicalDataScope.SYNTHETIC_REVIEW))}}}
        compose.onNodeWithText("選擇輸出資料夾…").performClick()
        awaitNode{it.packageName?.toString()=="com.google.android.documentsui"}
        if(find{it.text?.toString()=="USE THIS FOLDER" && it.isEnabled} == null) {
            click{it.text?.toString()=="Documents"}
        }
        click{it.text?.toString()=="USE THIS FOLDER" && it.isEnabled}
        click{it.text?.toString().equals("Allow",ignoreCase=true) && it.isEnabled}
        compose.waitUntil(8000){store.outputFolder!=null}
        val tree=requireNotNull(store.outputFolder)
        assertEquals("primary:Documents",DocumentsContract.getTreeDocumentId(tree))
        assertTrue(context.contentResolver.persistedUriPermissions.any{it.uri==tree && it.isReadPermission && it.isWritePermission})
        val files=mapOf("image.jpg" to "synthetic-folder-fixture".toByteArray(),"overlay.jpg" to "synthetic-overlay-fixture".toByteArray())
        val name=requireNotNull(store.save(files))
        val before=children(tree).toSet();store.deliver(name)
        val written=children(tree).filter{it !in before};assertEquals(1,written.size)
        val restored=readChildren(written.single());files.forEach{(k,v)->assertArrayEquals(v,restored[k])}
        assertEquals(setOf("image.jpg","overlay.jpg"),restored.keys)
        state.writeText(JSONObject().put("tree",tree.toString()).put("name",name).put("destination",written.single().toString())
            .put("oldFolder",originalFolder?.toString() ?: JSONObject.NULL).put("oldEnabled",originalEnabled).toString())
    }
    @Test fun b_afterProcessRestartReadbackThenRevoke() {
        val saved=JSONObject(state.readText());val tree=Uri.parse(saved.getString("tree"));val name=saved.getString("name")
        assertEquals(tree,store.outputFolder)
        assertTrue(context.contentResolver.persistedUriPermissions.any{it.uri==tree && it.isReadPermission && it.isWritePermission})
        val destination=Uri.parse(saved.getString("destination"))
        assertArrayEquals(store.read(name).getValue("image.jpg"),readChildren(destination).getValue("image.jpg"))
        val before=children(tree).toSet();store.deliver(name)
        val second=children(tree).filter{it !in before};assertEquals(1,second.size)
        assertTrue(DocumentsContract.deleteDocument(context.contentResolver,second.single()))
        assertTrue(DocumentsContract.deleteDocument(context.contentResolver,destination))
        context.contentResolver.releasePersistableUriPermission(tree,Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        assertThrows(Exception::class.java){store.deliver(name)}
        assertTrue(store.list().contains(name));store.read(name)
        File(store.directory,name).delete()
        store.usePrivateFolder();store.enabled=saved.getBoolean("oldEnabled")
        // Reference emulator test requires a previously unset custom folder.
        assertTrue(saved.isNull("oldFolder"));state.delete()
    }
}
