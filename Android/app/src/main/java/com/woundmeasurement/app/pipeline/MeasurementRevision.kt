package com.woundmeasurement.app.pipeline

import com.woundmeasurement.app.data.entity.MeasurementEntity

/** Resaving is not a new capture. A receipt survives only if content and bindings are identical. */
object MeasurementRevision {
    fun forResave(previous: MeasurementEntity, proposed: MeasurementEntity,
                  sameImageBytes: Boolean, sameRasterBytes: Boolean): MeasurementEntity {
        require(previous.id == proposed.id)
        val comparable = proposed.copy(timestamp = previous.timestamp, notes = previous.notes,
            imagePath = previous.imagePath, rasterPath = previous.rasterPath,
            annotationSubmitted = previous.annotationSubmitted)
        val unchanged = sameImageBytes && sameRasterBytes && comparable == previous
        return proposed.copy(timestamp = previous.timestamp,
            annotationSubmitted = previous.annotationSubmitted && unchanged)
    }
}
