package com.auri.chat

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "health_metrics")
data class HealthMetricEntity(
    @PrimaryKey val id: String,
    val metricType: String,
    val day: String,
    val value1: Double?,
    val value2: Double?,
    val value3: Double?,
    val source: String? = null,
    val sourceUpdatedAt: Long = 0L,
    val resolutionPolicy: String? = null,
    val updatedAt: Long,
)

@Entity(tableName = "health_samples")
data class HealthSampleEntity(
    @PrimaryKey val id: String,
    val metricType: String,
    val day: String,
    val bucketStart: String,
    val bucketEnd: String,
    val value1: Double?,
    val value2: Double?,
    val value3: Double?,
    val value4: String? = null,
    val updatedAt: Long,
)

@Entity(tableName = "sleep_scores")
data class SleepScoreEntity(
    @PrimaryKey val id: String,
    val sleepDay: String,
    val sessionStart: String,
    val sessionEnd: String,
    val sleepHealthScore: Int?,
    val sleepHealthConfidence: Int,
    val sleepHealthStatus: String,
    val sleepHealthComponents: String,
    val recoveryScore: Int?,
    val recoveryConfidence: Int,
    val recoveryStatus: String,
    val recoveryComponents: String,
    val recoveryMissing: String,
    val calibrationState: String,
    val calibrationDay: Int?,
    val calibrationTotalDays: Int,
    val validNights: Int,
    val algorithmVersion: String,
    val vendorScore: Int?,
    val computedAt: Long,
)

@Entity(tableName = "chat_messages")
data class ChatMessageEntity(
    @PrimaryKey val id: String,
    val role: String,
    val content: String,
    val images: String = "[]",
    val files: String = "[]",
    val actions: String = "[]",
    val isUser: Boolean,
    val isError: Boolean = false,
    val deliveryStatus: String = "sent",
    val pendingPayload: String = "",
    val timestamp: Long,
)

@Dao
interface HealthMetricDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(metrics: List<HealthMetricEntity>)

    @Query("SELECT * FROM health_metrics WHERE metricType = :type AND day >= :fromDay AND day <= :toDay ORDER BY day ASC")
    suspend fun getRange(type: String, fromDay: String, toDay: String): List<HealthMetricEntity>

    @Query("SELECT * FROM health_metrics WHERE metricType = :type ORDER BY day DESC LIMIT 1")
    suspend fun getLatest(type: String): HealthMetricEntity?

    @Query("DELETE FROM health_metrics WHERE metricType = :type AND day >= :fromDay AND day <= :toDay")
    suspend fun deleteRange(type: String, fromDay: String, toDay: String)

    @Query("DELETE FROM health_metrics")
    suspend fun clearAll()
}

@Dao
interface HealthSampleDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(samples: List<HealthSampleEntity>)

    @Query("SELECT * FROM health_samples WHERE metricType = :type AND day >= :fromDay AND day <= :toDay ORDER BY day ASC, bucketStart ASC")
    suspend fun getRange(type: String, fromDay: String, toDay: String): List<HealthSampleEntity>

    @Query("SELECT * FROM health_samples WHERE metricType = :type ORDER BY bucketStart DESC LIMIT 1")
    suspend fun getLatestSample(type: String): HealthSampleEntity?

    @Query("DELETE FROM health_samples WHERE metricType = :type AND day >= :fromDay AND day <= :toDay")
    suspend fun deleteRange(type: String, fromDay: String, toDay: String)

    @Query("DELETE FROM health_samples")
    suspend fun clearAll()
}

@Dao
interface SleepScoreDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(scores: List<SleepScoreEntity>)

    @Query("SELECT * FROM sleep_scores WHERE sleepDay >= :fromDay AND sleepDay <= :toDay ORDER BY sleepDay ASC")
    suspend fun getRange(fromDay: String, toDay: String): List<SleepScoreEntity>

    @Query("SELECT * FROM sleep_scores ORDER BY sleepDay DESC LIMIT 1")
    suspend fun getLatest(): SleepScoreEntity?

    @Query("DELETE FROM sleep_scores WHERE sleepDay >= :fromDay AND sleepDay <= :toDay")
    suspend fun deleteRange(fromDay: String, toDay: String)

    @Query("DELETE FROM sleep_scores")
    suspend fun clearAll()
}

@Dao
interface ChatMessageDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(message: ChatMessageEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(messages: List<ChatMessageEntity>)

    @Query("SELECT * FROM chat_messages ORDER BY timestamp ASC")
    suspend fun getAll(): List<ChatMessageEntity>

    @Query("SELECT * FROM chat_messages ORDER BY timestamp ASC")
    fun observeAll(): Flow<List<ChatMessageEntity>>

    @Query("DELETE FROM chat_messages WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("DELETE FROM chat_messages")
    suspend fun clearAll()
}

@Database(
    entities = [
        HealthMetricEntity::class,
        HealthSampleEntity::class,
        SleepScoreEntity::class,
        ChatMessageEntity::class,
    ],
    version = 10,
    exportSchema = false,
)
abstract class AuriDatabase : RoomDatabase() {
    abstract fun healthMetricDao(): HealthMetricDao
    abstract fun healthSampleDao(): HealthSampleDao
    abstract fun sleepScoreDao(): SleepScoreDao
    abstract fun chatMessageDao(): ChatMessageDao

    companion object {
        @Volatile
        private var instance: AuriDatabase? = null

        private val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE chat_messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'",
                )
            }
        }

        private val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE chat_messages ADD COLUMN files TEXT NOT NULL DEFAULT '[]'",
                )
            }
        }

        private val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE chat_messages ADD COLUMN actions TEXT NOT NULL DEFAULT '[]'",
                )
            }
        }

        private val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `sleep_scores` (
                        `id` TEXT NOT NULL,
                        `sleepDay` TEXT NOT NULL,
                        `sessionStart` TEXT NOT NULL,
                        `sessionEnd` TEXT NOT NULL,
                        `sleepHealthScore` INTEGER,
                        `sleepHealthConfidence` INTEGER NOT NULL,
                        `sleepHealthStatus` TEXT NOT NULL,
                        `sleepHealthComponents` TEXT NOT NULL,
                        `recoveryScore` INTEGER,
                        `recoveryConfidence` INTEGER NOT NULL,
                        `recoveryStatus` TEXT NOT NULL,
                        `recoveryComponents` TEXT NOT NULL,
                        `recoveryMissing` TEXT NOT NULL,
                        `calibrationState` TEXT NOT NULL,
                        `calibrationDay` INTEGER,
                        `calibrationTotalDays` INTEGER NOT NULL,
                        `validNights` INTEGER NOT NULL,
                        `algorithmVersion` TEXT NOT NULL,
                        `vendorScore` INTEGER,
                        `computedAt` INTEGER NOT NULL,
                        PRIMARY KEY(`id`)
                    )
                    """.trimIndent(),
                )
            }
        }

        private val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE health_metrics ADD COLUMN source TEXT")
                db.execSQL(
                    "ALTER TABLE health_metrics ADD COLUMN sourceUpdatedAt INTEGER NOT NULL DEFAULT 0",
                )
                db.execSQL("ALTER TABLE health_metrics ADD COLUMN resolutionPolicy TEXT")
            }
        }

        private val MIGRATION_9_10 = object : Migration(9, 10) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE chat_messages ADD COLUMN deliveryStatus TEXT NOT NULL DEFAULT 'sent'",
                )
                db.execSQL(
                    "ALTER TABLE chat_messages ADD COLUMN pendingPayload TEXT NOT NULL DEFAULT ''",
                )
            }
        }

        fun get(context: Context): AuriDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    AuriDatabase::class.java,
                    "auri.db",
                )
                    .addMigrations(
                        MIGRATION_4_5,
                        MIGRATION_5_6,
                        MIGRATION_6_7,
                        MIGRATION_7_8,
                        MIGRATION_8_9,
                        MIGRATION_9_10,
                    )
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}
