# SQLite to PostgreSQL Migration Plan - Zero Downtime

## Overview

This plan migrates Mahoraga from SQLite to PostgreSQL with zero downtime using a dual-write strategy with eventual consistency.

## Current State Analysis

**Databases:**
- Main: `~/.mahoraga/mahoraga.db` (aiosqlite, async)
- Routing: `~/.mahoraga/routing_decisions.db` (sqlite3, sync)

**Key Tables:**
- `missions` - Core mission data
- `tasks` - Task execution records  
- `task_metrics` - Performance metrics
- `decisions` - Routing decisions
- `chat_log` - Conversation history
- `artifacts` - File attachments
- `events` - System events

## Migration Strategy: Dual-Write with Read Preference

### Phase 1: Infrastructure Setup (0 downtime)

1. **PostgreSQL Setup**
   ```bash
   # Docker Compose for local dev
   docker run -d \
     --name mahoraga-postgres \
     -e POSTGRES_DB=mahoraga \
     -e POSTGRES_USER=mahoraga \
     -e POSTGRES_PASSWORD=secure_password \
     -p 5432:5432 \
     postgres:15
   ```

2. **Schema Translation**
   - Convert SQLite schema to PostgreSQL
   - Handle data type differences (TEXT → VARCHAR, REAL → NUMERIC)
   - Add proper indexes and constraints
   - Create migration scripts

3. **Connection Pool Setup**
   ```python
   # New PostgreSQL connection manager
   import asyncpg
   from asyncpg.pool import Pool
   
   class PostgreSQLStore:
       def __init__(self, pool: Pool):
           self._pool = pool
   ```

### Phase 2: Dual-Write Implementation (0 downtime)

1. **Abstract Database Interface**
   ```python
   from abc import ABC, abstractmethod
   
   class DatabaseBackend(ABC):
       @abstractmethod
       async def save_task(self, task: Task) -> None: ...
       
       @abstractmethod  
       async def get_task(self, task_id: str) -> Task | None: ...
   
   class SQLiteBackend(DatabaseBackend): ...
   class PostgreSQLBackend(DatabaseBackend): ...
   ```

2. **Dual-Write Store Wrapper**
   ```python
   class DualWriteStore:
       def __init__(self, primary: DatabaseBackend, secondary: DatabaseBackend):
           self.primary = primary      # SQLite (current)
           self.secondary = secondary  # PostgreSQL (new)
           
       async def save_task(self, task: Task) -> None:
           # Write to primary first (consistency)
           await self.primary.save_task(task)
           
           # Write to secondary (best effort)
           try:
               await self.secondary.save_task(task)
           except Exception as e:
               logger.warning(f"Secondary write failed: {e}")
               # Queue for retry
               await self._queue_retry(task)
   ```

3. **Retry Mechanism**
   ```python
   class RetryQueue:
       async def queue_retry(self, operation: str, data: dict):
           # Store failed operations for background retry
           pass
           
       async def process_retries(self):
           # Background task to sync missed writes
           pass
   ```

### Phase 3: Data Migration (0 downtime)

1. **Initial Bulk Copy**
   ```python
   async def migrate_existing_data():
       sqlite_store = await Store.connect()
       pg_store = await PostgreSQLStore.connect()
       
       # Copy in batches to avoid memory issues
       batch_size = 1000
       
       for table in ['missions', 'tasks', 'task_metrics', 'decisions']:
           await copy_table_data(sqlite_store, pg_store, table, batch_size)
   ```

2. **Consistency Verification**
   ```python
   async def verify_data_consistency():
       # Compare record counts
       # Spot check random samples
       # Validate critical business data
       pass
   ```

### Phase 4: Read Migration (0 downtime)

1. **Gradual Read Shift**
   ```python
   class HybridStore:
       def __init__(self, sqlite_store, pg_store, pg_read_percentage=0):
           self.sqlite = sqlite_store
           self.postgres = pg_store
           self.pg_read_pct = pg_read_percentage
           
       async def get_task(self, task_id: str) -> Task | None:
           if random.random() < self.pg_read_pct:
               try:
                   return await self.postgres.get_task(task_id)
               except Exception:
                   # Fallback to SQLite
                   return await self.sqlite.get_task(task_id)
           else:
               return await self.sqlite.get_task(task_id)
   ```

2. **Monitoring & Rollback**
   ```python
   # Monitor error rates, latency, data consistency
   # Automatic rollback if PostgreSQL issues detected
   ```

### Phase 5: Complete Migration (brief maintenance window)

1. **Final Sync**
   - Stop writes briefly
   - Sync any remaining differences
   - Switch to PostgreSQL-only

2. **Cleanup**
   - Remove dual-write code
   - Archive SQLite files
   - Update configuration

## Implementation Details

### Schema Conversion

```sql
-- SQLite to PostgreSQL mappings
TEXT → VARCHAR or TEXT
REAL → NUMERIC or DOUBLE PRECISION  
INTEGER → INTEGER or BIGINT
BLOB → BYTEA

-- Add proper constraints
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_run_id 
  FOREIGN KEY (run_id) REFERENCES missions(id);

-- Add indexes for performance
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_metrics_timestamp ON task_metrics(timestamp);
```

### Configuration Management

```python
# config.py
@dataclass
class DatabaseConfig:
    sqlite_path: Path = Path("~/.mahoraga/mahoraga.db")
    postgres_url: str = "postgresql://user:pass@localhost/mahoraga"
    migration_mode: str = "sqlite_only"  # sqlite_only, dual_write, postgres_only
    postgres_read_percentage: int = 0
```

### Error Handling

```python
class MigrationError(Exception): pass
class ConsistencyError(MigrationError): pass
class SyncError(MigrationError): pass

# Comprehensive error handling with automatic fallbacks
# Detailed logging for troubleshooting
# Health checks and monitoring
```

## Rollback Strategy

1. **Immediate Rollback**: Set `migration_mode = "sqlite_only"`
2. **Data Recovery**: SQLite remains authoritative during migration
3. **Monitoring**: Automated alerts for consistency issues

## Testing Strategy

1. **Unit Tests**: Test dual-write logic, error handling
2. **Integration Tests**: Full migration simulation
3. **Load Tests**: Performance under dual-write load
4. **Chaos Tests**: Network failures, database unavailability

## Implementation Roadmap

### Pre-Migration Checklist
- [ ] PostgreSQL instance provisioned and configured
- [ ] Database connection pooling tested
- [ ] Schema conversion scripts validated
- [ ] Backup strategy verified
- [ ] Monitoring dashboards configured
- [ ] Rollback procedures documented and tested

### Phase-by-Phase Execution

#### Week 1: Foundation (0 downtime)
- [ ] Set up PostgreSQL instance with proper configuration
- [ ] Create schema conversion scripts and validate against SQLite
- [ ] Implement abstract database interface
- [ ] Add PostgreSQL dependencies to requirements.txt
- [ ] Create configuration flags for migration modes

#### Week 2: Dual-Write Infrastructure (0 downtime)  
- [ ] Implement dual-write store wrapper
- [ ] Add retry queue for failed secondary writes
- [ ] Create consistency verification tools
- [ ] Add comprehensive logging and metrics
- [ ] Deploy with `migration_mode = "sqlite_only"` (no behavior change)

#### Week 3: Data Migration (0 downtime)
- [ ] Enable dual-write mode: `migration_mode = "dual_write"`
- [ ] Run initial bulk data migration script
- [ ] Verify data consistency between databases
- [ ] Monitor write performance and error rates
- [ ] Fine-tune retry mechanisms

#### Week 4: Read Migration (0 downtime)
- [ ] Gradually increase PostgreSQL read percentage: 10% → 25% → 50% → 75% → 90%
- [ ] Monitor query performance and error rates at each step
- [ ] Validate business-critical queries work correctly
- [ ] Run consistency checks daily
- [ ] Prepare for final cutover

#### Week 5: Complete Migration (brief maintenance)
- [ ] Schedule 5-minute maintenance window
- [ ] Stop writes, perform final sync
- [ ] Switch to `migration_mode = "postgres_only"`
- [ ] Verify all functionality works
- [ ] Archive SQLite files
- [ ] Remove dual-write code in follow-up release

### Daily Operations During Migration

#### Monitoring Checklist
```bash
# Check dual-write success rates
SELECT 
  COUNT(*) as total_writes,
  COUNT(CASE WHEN postgres_success = true THEN 1 END) as pg_success,
  COUNT(CASE WHEN sqlite_success = true THEN 1 END) as sqlite_success
FROM write_log 
WHERE timestamp > NOW() - INTERVAL '1 hour';

# Verify data consistency
python -m backend.orchestrator.tools.consistency_check

# Monitor connection pool health
python -m backend.orchestrator.tools.db_health_check
```

#### Alert Thresholds
- PostgreSQL write failure rate > 5%
- Data consistency check failures
- Connection pool exhaustion
- Query latency increase > 50%

## Timeline

- **Week 1**: Infrastructure setup, schema conversion
- **Week 2**: Dual-write implementation, testing
- **Week 3**: Data migration, consistency verification  
- **Week 4**: Gradual read migration (10% → 50% → 90%)
- **Week 5**: Complete migration, cleanup

## Monitoring & Alerts

```python
# Key metrics to monitor
- Write latency (SQLite vs PostgreSQL)
- Error rates by database
- Data consistency checks
- Query performance
- Connection pool health
```

## Operational Procedures

### Emergency Rollback Procedure
```python
# Immediate rollback (< 30 seconds)
# 1. Update configuration
config.migration_mode = "sqlite_only"
config.postgres_read_percentage = 0

# 2. Restart application or reload config
await app.reload_database_config()

# 3. Verify SQLite is handling all traffic
await verify_sqlite_only_mode()
```

### Data Consistency Verification
```python
# Daily consistency check script
async def verify_consistency():
    sqlite_count = await sqlite_store.count_records("tasks")
    pg_count = await postgres_store.count_records("tasks")
    
    if abs(sqlite_count - pg_count) > 10:  # Allow small drift
        alert("Data consistency issue detected")
        
    # Sample verification
    sample_ids = await get_random_task_ids(100)
    for task_id in sample_ids:
        sqlite_task = await sqlite_store.get_task(task_id)
        pg_task = await postgres_store.get_task(task_id)
        
        if not tasks_equal(sqlite_task, pg_task):
            alert(f"Task {task_id} inconsistent between databases")
```

### Performance Monitoring
```python
# Real-time performance metrics
@dataclass
class MigrationMetrics:
    sqlite_write_latency: float
    postgres_write_latency: float
    dual_write_success_rate: float
    postgres_read_success_rate: float
    consistency_check_status: bool
    
async def collect_metrics() -> MigrationMetrics:
    # Collect from application metrics
    return MigrationMetrics(...)

# Alert if PostgreSQL performance degrades
if metrics.postgres_write_latency > metrics.sqlite_write_latency * 2:
    alert("PostgreSQL write performance degraded")
```

### Migration Health Dashboard
```python
# Key metrics to display
- Current migration phase
- Read/write split percentages  
- Success rates by database
- Recent consistency check results
- Connection pool status
- Error rates and recent failures
```

## Additional Considerations

### Connection Pool Optimization
```python
# PostgreSQL connection pool configuration
POSTGRES_POOL_CONFIG = {
    'min_size': 5,
    'max_size': 20,
    'max_queries': 50000,
    'max_inactive_connection_lifetime': 300,
    'command_timeout': 60,
    'server_settings': {
        'application_name': 'mahoraga',
        'jit': 'off'  # Disable JIT for consistent performance
    }
}
```

### Transaction Handling
```python
class TransactionManager:
    async def dual_write_transaction(self, operations: List[Operation]):
        """Execute operations in both databases with proper rollback"""
        sqlite_tx = await self.sqlite.begin()
        postgres_tx = await self.postgres.begin()
        
        try:
            # Execute in SQLite first (authoritative)
            for op in operations:
                await op.execute(sqlite_tx)
            await sqlite_tx.commit()
            
            # Then PostgreSQL (best effort)
            for op in operations:
                await op.execute(postgres_tx)
            await postgres_tx.commit()
            
        except Exception as e:
            await sqlite_tx.rollback()
            await postgres_tx.rollback()
            raise
```

### Data Validation Rules
```python
# Critical data validation during migration
VALIDATION_RULES = {
    'missions': {
        'required_fields': ['id', 'status', 'created_at'],
        'foreign_keys': [],
        'unique_constraints': ['id']
    },
    'tasks': {
        'required_fields': ['id', 'run_id', 'status'],
        'foreign_keys': [('run_id', 'missions.id')],
        'unique_constraints': ['id']
    },
    'task_metrics': {
        'required_fields': ['task_id', 'timestamp'],
        'foreign_keys': [('task_id', 'tasks.id')],
        'data_ranges': {'timestamp': 'last_30_days'}
    }
}
```

### Backup Strategy
```python
# Automated backup before each migration phase
async def create_migration_backup():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # SQLite backup
    sqlite_backup = f"~/.mahoraga/backups/sqlite_backup_{timestamp}.db"
    await backup_sqlite_database(sqlite_backup)
    
    # PostgreSQL backup (if data exists)
    if await postgres_has_data():
        pg_backup = f"~/.mahoraga/backups/postgres_backup_{timestamp}.sql"
        await backup_postgres_database(pg_backup)
    
    return {'sqlite': sqlite_backup, 'postgres': pg_backup}
```

### Performance Benchmarking
```python
# Benchmark queries before and after migration
BENCHMARK_QUERIES = [
    "SELECT COUNT(*) FROM tasks WHERE status = 'completed'",
    "SELECT * FROM missions ORDER BY created_at DESC LIMIT 100",
    "SELECT task_id, AVG(duration) FROM task_metrics GROUP BY task_id",
    "SELECT * FROM chat_log WHERE timestamp > NOW() - INTERVAL '1 day'"
]

async def run_performance_benchmark(store: DatabaseBackend) -> Dict[str, float]:
    results = {}
    for query in BENCHMARK_QUERIES:
        start_time = time.time()
        await store.execute_query(query)
        results[query] = time.time() - start_time
    return results
```

## Benefits

- **Zero Downtime**: Service remains available throughout
- **Safe Rollback**: SQLite remains authoritative
- **Gradual Migration**: Risk mitigation through phases
- **Performance Validation**: Real traffic testing
- **Data Integrity**: Comprehensive consistency checks

This approach ensures a smooth transition while maintaining system reliability and data integrity.