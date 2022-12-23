import csv
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple, Type
from uuid import UUID, uuid4

from pgcopy import CopyManager
from sqlalchemy import column as sacolumn
from sqlalchemy import func
from sqlalchemy import types as satypes
from sqlalchemy.orm import Session
from sqlalchemy.schema import Column as SAColumn
from sqlalchemy.schema import CreateTable, DropTable, MetaData, Identity  # type: ignore
from sqlalchemy.schema import Table as SATable
from sqlalchemy.sql.expression import TableClause, select
from sqlalchemy.sql.expression import table as satable
from sqlalchemy.sql.expression import text

from . import conv
from .db import engine
from .value_objs import (
    Column,
    ColumnType,
    KeySet,
    Page,
    PythonType,
    Row,
    Table,
    UserSubmittedCSVData,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowProxy


class PGUserdataAdapter:
    @staticmethod
    def make_temp_table_name() -> str:
        # FIXME: this name should probably include the date and some other helpful
        # info for debugging
        return f"temp_{uuid4().hex}"

    @staticmethod
    def get_tableclause(
        table_name: str, columns: Sequence[Column], schema: Optional[str] = None
    ) -> TableClause:
        return satable(  # type: ignore
            table_name,
            *[sacolumn(c.name, type_=c.type_.sqla_type()) for c in columns],
            schema=schema,
        )

    @staticmethod
    def make_userdata_table_name(table_uuid: UUID, with_schema=False) -> str:
        if with_schema:
            return f"userdata.table_{table_uuid.hex}"
        else:
            return f"table_{table_uuid.hex}"

    @classmethod
    def get_columns(cls, sesh: Session, table_uuid: UUID) -> List["Column"]:
        # lifted from https://dba.stackexchange.com/a/22420/28877
        attrelid = cls.make_userdata_table_name(table_uuid, with_schema=True)
        stmt = text(
            """
        SELECT attname AS column_name, atttypid::regtype AS sql_type
        FROM   pg_attribute
        WHERE  attrelid = :table_name ::regclass
        AND    attnum > 0
        AND    NOT attisdropped
        ORDER  BY attnum
        """
        )
        rs = sesh.execute(stmt, {"table_name": attrelid})
        rv = []
        for name, sql_type in rs:
            rv.append(Column(name=name, type_=ColumnType.from_sql_type(sql_type)))
        return rv

    @classmethod
    def get_userdata_tableclause(cls, sesh: Session, table_uuid: UUID) -> TableClause:
        columns = cls.get_columns(sesh, table_uuid)
        table_name = cls.make_userdata_table_name(table_uuid)
        return cls.get_tableclause(table_name, columns, schema="userdata")

    @classmethod
    def get_row(cls, sesh: Session, table_uuid: UUID, row_id: int) -> Optional[Row]:
        columns = cls.get_columns(sesh, table_uuid)
        table_clause = cls.get_userdata_tableclause(sesh, table_uuid)
        cursor = sesh.execute(
            table_clause.select().where(table_clause.c.csvbase_row_id == row_id)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        else:
            return {c: row[c.name] for c in columns}

    @classmethod
    def get_a_sample_row(cls, sesh: Session, table_uuid: UUID) -> Row:
        """Returns a sample row from the table (the lowest row id).

        If none exist, a made-up row is returned.  This function is for
        example/documentation purposes only."""
        columns = cls.get_columns(sesh, table_uuid)
        table_clause = cls.get_userdata_tableclause(sesh, table_uuid)
        cursor = sesh.execute(table_clause.select().order_by("csvbase_row_id").limit(1))
        row = cursor.fetchone()
        if row is None:
            # return something made-up
            return {c: c.type_.example() for c in columns}
        else:
            return {c: row[c.name] for c in columns}

    @classmethod
    def insert_row(cls, sesh: Session, table_uuid: UUID, row: Row) -> int:
        table = cls.get_userdata_tableclause(sesh, table_uuid)
        values = {c.name: v for c, v in row.items()}
        return sesh.execute(
            table.insert().values(values).returning(table.c.csvbase_row_id)
        ).scalar()

    @classmethod
    def update_row(
        cls,
        sesh: Session,
        table_uuid: UUID,
        row_id: int,
        row: Row,
    ) -> bool:
        """Update a given row, returning True if it existed (and was updated) and False otherwise."""
        table = cls.get_userdata_tableclause(sesh, table_uuid)
        values = {c.name: v for c, v in row.items()}
        result = sesh.execute(
            table.update().where(table.c.csvbase_row_id == row_id).values(values)
        )
        return result.rowcount > 0

    @classmethod
    def delete_row(cls, sesh: Session, table_uuid: UUID, row_id: int) -> bool:
        """Update a given row, returning True if it existed (and was updated) and False otherwise."""
        table = cls.get_userdata_tableclause(sesh, table_uuid)
        result = sesh.execute(table.delete().where(table.c.csvbase_row_id == row_id))
        return result.rowcount > 0

    @classmethod
    def table_page(
        cls, sesh: Session, username: str, table: Table, keyset: KeySet
    ) -> Page:
        """Get a page from a table based on the provided KeySet"""
        # FIXME: this doesn't handle empty tables
        table_clause = cls.get_userdata_tableclause(sesh, table.table_uuid)
        if keyset.op == "greater_than":
            where_cond = table_clause.c.csvbase_row_id > keyset.n
        else:
            where_cond = table_clause.c.csvbase_row_id < keyset.n

        keyset_page = table_clause.select().where(where_cond).limit(keyset.size)

        if keyset.op == "greater_than":
            keyset_page = keyset_page.order_by(table_clause.c.csvbase_row_id)
        else:
            # if we're going backwards we need to reverse the order via a subquery
            keyset_page = keyset_page.order_by(table_clause.c.csvbase_row_id.desc())
            keyset_sub = select(keyset_page.alias())  # type: ignore
            keyset_page = keyset_sub.order_by("csvbase_row_id")

        row_tuples: List[RowProxy] = list(sesh.execute(keyset_page))

        if len(row_tuples) > 1:
            has_more_q = (
                table_clause.select().where(table_clause.c.csvbase_row_id > row_tuples[-1].csvbase_row_id).exists()  # type: ignore
            )
            has_more = sesh.query(has_more_q).scalar()
            has_less_q = (
                table_clause.select().where(table_clause.c.csvbase_row_id < row_tuples[0].csvbase_row_id).exists()  # type: ignore
            )
            has_less = sesh.query(has_less_q).scalar()
        else:
            if keyset.op == "greater_than":
                has_more = False
                has_less = sesh.query(
                    table_clause.select().where(table_clause.c.csvbase_row_id < keyset.n).exists()  # type: ignore
                ).scalar()
            else:
                has_more = sesh.query(
                    table_clause.select().where(table_clause.c.csvbase_row_id > keyset.n).exists()  # type: ignore
                ).scalar()
                has_less = False

        rows = [{c: row_tup[c.name] for c in table.columns} for row_tup in row_tuples]

        return Page(
            has_less=has_less,
            has_more=has_more,
            rows=rows[: keyset.size],
        )

    @classmethod
    def table_as_rows(
        cls,
        sesh: Session,
        table_uuid: UUID,
    ) -> Iterable[Tuple[PythonType]]:
        table_clause = cls.get_userdata_tableclause(sesh, table_uuid)
        columns = cls.get_columns(sesh, table_uuid)
        q = select([getattr(table_clause.c, c.name) for c in columns]).order_by(
            table_clause.c.csvbase_row_id
        )
        yield from sesh.execute(q)

    @classmethod
    def insert_table_data(
        cls,
        sesh: Session,
        user_uuid: UUID,
        username: str,
        table: Table,
        csv_buf: UserSubmittedCSVData,
        dialect: Type[csv.Dialect],
        columns: Sequence[Column],
    ) -> None:
        # Copy in with binary copy
        reader = csv.reader(csv_buf, dialect)
        csv_buf.readline()  # pop the header, which is not useful
        row_gen = (
            [conv.from_string_to_python(col.type_, v) for col, v in zip(columns, line)]
            for line in reader
        )

        raw_conn = sesh.connection().connection
        cols = [c.name for c in table.user_columns()]
        copy_manager = CopyManager(
            raw_conn,
            cls.make_userdata_table_name(table.table_uuid, with_schema=True),
            cols,
        )
        copy_manager.copy(row_gen)

    @classmethod
    def drop_table(cls, sesh: Session, table_uuid: UUID) -> None:
        # FIXME: dead code
        # FIXME: get_userdata_tableclause ?
        # sa_table = SATable(
        #     make_userdata_table_name(table_uuid),
        #     MetaData(bind=engine),
        #     schema="userdata",
        # )
        sa_table = cls.get_userdata_tableclause(sesh, table_uuid)
        sesh.execute(DropTable(sa_table))  # type: ignore

    @classmethod
    def create_table(
        cls, sesh: Session, username: str, table_name: str, columns: Iterable[Column]
    ) -> UUID:
        table_uuid = uuid4()
        cols: List[SAColumn] = [
            SAColumn(
                "csvbase_row_id", satypes.BigInteger, Identity(), primary_key=True
            ),
            # FIXME: would be good to have these two columns plus
            # "csvbase_created_by" and csvbase_updated_by, but needs support for
            # datetimes as a type
            # SAColumn(
            #     "csvbase_created",
            #     type_=satypes.TIMESTAMP(timezone=True),
            #     nullable=False,
            #     default="now()",
            # ),
            # SAColumn(
            #     "csvbase_update",
            #     type_=satypes.TIMESTAMP(timezone=True),
            #     nullable=False,
            #     default="now()",
            # ),
        ]
        for col in columns:
            cols.append(SAColumn(col.name, type_=col.type_.sqla_type()))
        table = SATable(
            cls.make_userdata_table_name(table_uuid),
            MetaData(bind=engine),
            *cols,
            schema="userdata",
        )
        sesh.execute(CreateTable(table))
        return table_uuid

    @classmethod
    def make_drop_table_ddl(cls, sesh: Session, table_uuid: UUID) -> DropTable:
        sqla_table = cls.get_userdata_tableclause(sesh, table_uuid)
        # sqlalchemy-stubs doesn't match sqla 1.4
        return DropTable(sqla_table)  # type: ignore

    @classmethod
    def upsert_table_data(
        cls,
        sesh: Session,
        table: Table,
        csv_buf: UserSubmittedCSVData,
        dialect: Type[csv.Dialect],
    ) -> None:
        """Upsert table data, using the csvbase_row_id to correlate the submitted
        csv with the extant table.

        """
        # This is a complicated routine.  First make a generator for the incoming rows.
        reader = csv.reader(csv_buf, dialect)
        csv_buf.readline()  # pop the header, which is not useful
        row_gen = (
            [
                conv.from_string_to_python(col.type_, v)
                for col, v in zip(table.columns, line)
            ]
            for line in reader
        )

        # Then make a temp table and COPY the new rows into it
        temp_table_name = cls.make_temp_table_name()
        main_table_name = cls.make_userdata_table_name(
            table.table_uuid, with_schema=True
        )

        # The below 'nosec' location and fmt pragma are hacks to work around
        # https://github.com/PyCQA/bandit/issues/658
        # This only works on bandit 1.6.3
        # fmt: off
        """# nosec"""; create_temp_table_ddl = text(f"""
    CREATE temp TABLE "{temp_table_name}" ON COMMIT DROP AS
    SELECT
        *
    FROM
        {main_table_name}
    LIMIT 0;
    """)
        # fmt: on
        sesh.execute(create_temp_table_ddl)
        raw_conn = sesh.connection().connection
        column_names = [c.name for c in table.columns]
        copy_manager = CopyManager(raw_conn, temp_table_name, column_names)
        copy_manager.copy(row_gen)

        # Next selectively use the temp table to update the 'main' one
        main_tableclause = cls.get_userdata_tableclause(sesh, table.table_uuid)
        temp_tableclause = cls.get_tableclause(temp_table_name, table.columns)

        # 1. for removals
        remove_stmt = main_tableclause.delete().where(
            main_tableclause.c.csvbase_row_id.not_in(  # type: ignore
                select(temp_tableclause.c.csvbase_row_id)  # type: ignore
            )
        )

        # 2. updates
        update_stmt = (
            main_tableclause.update()
            .values(
                **{
                    col.name: getattr(temp_tableclause.c, col.name)
                    for col in table.columns
                }
            )
            .where(
                main_tableclause.c.csvbase_row_id == temp_tableclause.c.csvbase_row_id
            )
        )

        # 3a. and additions where the csvbase_row_id as been set
        add_stmt_select_columns = [
            getattr(temp_tableclause.c, c.name) for c in table.columns
        ]
        add_stmt_no_blanks = main_tableclause.insert().from_select(
            column_names,
            select(*add_stmt_select_columns)  # type: ignore
            .select_from(
                temp_tableclause.outerjoin(
                    main_tableclause,
                    main_tableclause.c.csvbase_row_id
                    == temp_tableclause.c.csvbase_row_id,
                )
            )
            .where(
                main_tableclause.c.csvbase_row_id.is_(None),
                temp_tableclause.c.csvbase_row_id.is_not(None),  # type: ignore
            ),
        )

        # 3b. now reseting the sequence that allocates pks
        reset_serial_stmt = select(
            func.setval(
                func.pg_get_serial_sequence(main_table_name, "csvbase_row_id"),
                func.max(main_tableclause.c.csvbase_row_id),
            )
        )

        # 3c. additions which do not have a csvbase_row_id set
        select_columns = [
            func.coalesce(
                func.nextval(
                    func.pg_get_serial_sequence(main_table_name, "csvbase_row_id")
                )
            )
        ]
        select_columns += [
            getattr(temp_tableclause.c, c.name) for c in table.user_columns()
        ]
        add_stmt_blanks = main_tableclause.insert().from_select(
            column_names,
            select(*select_columns)  # type: ignore
            .select_from(
                temp_tableclause.outerjoin(
                    main_tableclause,
                    main_tableclause.c.csvbase_row_id
                    == temp_tableclause.c.csvbase_row_id,
                )
            )
            .where(
                main_tableclause.c.csvbase_row_id.is_(None),
                temp_tableclause.c.csvbase_row_id.is_(None),
            ),
        )
        sesh.execute(remove_stmt)
        sesh.execute(update_stmt)
        sesh.execute(add_stmt_no_blanks)
        sesh.execute(reset_serial_stmt)
        sesh.execute(add_stmt_blanks)