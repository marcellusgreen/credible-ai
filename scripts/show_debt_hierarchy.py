#!/usr/bin/env python3
"""Show debt instruments within corporate hierarchy."""

import sys

from sqlalchemy import select

from script_utils import get_db_session, run_async
from app.models import Entity, Company, DebtInstrument


async def show_debt_in_hierarchy(ticker):
    async with get_db_session() as db:
        company = await db.scalar(select(Company).where(Company.ticker == ticker.upper()))
        if not company:
            print('Company not found')
            return

        # Get entities
        result = await db.execute(
            select(Entity).where(Entity.company_id == company.id)
        )
        entities = {e.id: e for e in result.scalars()}

        # Get debt instruments
        result = await db.execute(
            select(DebtInstrument).where(DebtInstrument.company_id == company.id)
        )
        debts = list(result.scalars())

        # Build children lookup
        children = {}
        for e in entities.values():
            if e.parent_id:
                if e.parent_id not in children:
                    children[e.parent_id] = []
                children[e.parent_id].append(e)

        # Build debt lookup by issuer
        debt_by_issuer = {}
        for d in debts:
            if d.issuer_id:
                if d.issuer_id not in debt_by_issuer:
                    debt_by_issuer[d.issuer_id] = []
                debt_by_issuer[d.issuer_id].append(d)

        print(f'{company.name} ({ticker.upper()})')
        print('=' * 80)
        print(f'Total entities: {len(entities)}')
        print(f'Total debt instruments: {len(debts)}')
        print(f'Debt with issuer assigned: {sum(1 for d in debts if d.issuer_id)}')
        print()

        def print_tree(entity, indent=0):
            prefix = '    ' * indent + ('|-- ' if indent > 0 else '')
            name = entity.name.encode('ascii', 'replace').decode('ascii')[:50]

            # Check if this entity has debt
            entity_debts = debt_by_issuer.get(entity.id, [])
            debt_marker = f' ** {len(entity_debts)} DEBT **' if entity_debts else ''

            print(f'{prefix}{name}{debt_marker}')

            # Show debt details
            for d in entity_debts:
                debt_name = (d.name or 'Unnamed')[:45]
                amount = f'${d.principal/1e6:.0f}M' if d.principal else ''
                indent_str = '    ' * (indent + 1)
                print(f'{indent_str}    -> {debt_name} {amount}')

            if entity.id in children:
                for child in sorted(children[entity.id], key=lambda x: x.name):
                    print_tree(child, indent + 1)

        # Find main root
        roots = [e for e in entities.values() if not e.parent_id]
        main_root = None
        for r in roots:
            if r.structure_tier == 1 or company.name.lower() in r.name.lower():
                main_root = r
                break
        if not main_root and roots:
            main_root = roots[0]

        if main_root:
            print('HIERARCHY WITH DEBT LOCATIONS:')
            print('-' * 80)
            print_tree(main_root)

        # Show unassigned debt
        unassigned = [d for d in debts if not d.issuer_id]
        if unassigned:
            print()
            print(f'DEBT WITHOUT ISSUER ASSIGNED ({len(unassigned)}):')
            print('-' * 80)
            for d in unassigned[:10]:
                name = d.name[:60] if d.name else 'Unnamed'
                print(f'  - {name}')
            if len(unassigned) > 10:
                print(f'  ... and {len(unassigned) - 10} more')


if __name__ == '__main__':
    ticker = sys.argv[1] if len(sys.argv) > 1 else 'UAL'
    run_async(show_debt_in_hierarchy(ticker))
