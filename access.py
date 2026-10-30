"""AU Schools role hierarchy, shared by ticket and moderation checks."""
STAFF_REVIEW_ROLE = 1563853560008015933
TICKET_STAFF_ROLE = 1563853559970136219
MODERATION_ROLE = 1563853559970136222
ACCEPTED_STAFF_ROLE = 1563853559970136219
JOIN_ROLE = 1563853559944974375


def at_or_above(member, role_id):
    if member is None:
        return False
    if member.id == member.guild.owner_id:
        return True
    role = member.guild.get_role(role_id)
    return bool(role and member.top_role >= role)


def ticket_threshold(kind):
    return STAFF_REVIEW_ROLE if kind in ('Staff Apply', 'Player/Staff Report') else TICKET_STAFF_ROLE


def ticket_staff(member, kind):
    return at_or_above(member, ticket_threshold(kind))
