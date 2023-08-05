from enum import Enum


class UserRole(Enum):
    ADMIN = 'admin'
    ADVANCED = 'advanced'
    BASIC = 'basic'
    STRANGER = 'stranger'


ROLE_ORDER = [UserRole.STRANGER, UserRole.BASIC, UserRole.ADVANCED, UserRole.ADMIN]


def check_role(required_role: UserRole, user_role: UserRole) -> bool:
    return ROLE_ORDER.index(user_role) >= ROLE_ORDER.index(required_role)
