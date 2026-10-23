"""SQLite storage: every table's schema, and the SQL that reads and writes it.

    core/storage/
      base.py      Store: a file, the schema it owns, CRUD of its entries
      util.py      statement builders and database inspection
      schemas/     one .sql file per schema, applied by name
      <store>.py   one Store per table or group of tables, holding all of their SQL

Domain code keeps what isn't SQL (JobManager's cache, the LangGraph
checkpointer, the vault sync parsers) and calls the stores. Agents keep their
own queries, but open the files through a store:

    with TasksStore(path).read_only() as conn:
        ...
"""
from core.storage.base import Store, StoreMissing, apply_schema, load_schema

__all__ = ["Store", "StoreMissing", "apply_schema", "load_schema"]
