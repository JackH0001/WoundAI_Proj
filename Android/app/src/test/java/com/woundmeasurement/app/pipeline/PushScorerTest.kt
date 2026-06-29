package com.woundmeasurement.app.pipeline

import org.junit.Assert.assertEquals
import org.junit.Test

/** 對齊 SSOT 金標(engineering/generated/push_golden.json)；三端計分須一致(差異=0)。 */
class PushScorerTest {
    @Test fun areaSubscoreBands() {
        val areas = doubleArrayOf(0.0, 0.2, 0.5, 0.9, 1.5, 2.5, 3.5, 6.0, 10.0, 20.0, 30.0)
        val expected = intArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
        for (i in areas.indices) assertEquals(expected[i], WoundPipeline.areaSubscore(areas[i]))
    }
    @Test fun tissueWorst() {
        assertEquals(4, WoundPipeline.tissueSubscore(mapOf("necrosis" to 0.1, "granulation" to 0.8)))
        assertEquals(2, WoundPipeline.tissueSubscore(mapOf("granulation" to 0.95)))
        assertEquals(0, WoundPipeline.tissueSubscore(mapOf("granulation" to 0.02)))
    }
    @Test fun pushGoldenCases() {
        val p1 = WoundPipeline.push(8.66, mapOf("granulation" to 0.78, "slough" to 0.14, "necrosis" to 0.08), 2)
        assertEquals(12, p1.partial); assertEquals(14, p1.full)
        val p2 = WoundPipeline.push(2.78, mapOf("slough" to 0.5, "granulation" to 0.4), 2)
        assertEquals(8, p2.partial); assertEquals(10, p2.full)
        val p3 = WoundPipeline.push(0.0, mapOf("granulation" to 1.0), null)
        assertEquals(2, p3.partial); assertEquals(null, p3.full)
    }
}
