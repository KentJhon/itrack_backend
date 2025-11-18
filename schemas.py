from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr

# ---- User/Role ----
class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: Optional[str] = None

class UpdateUserIn(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(
        None, description="If provided, will replace the user's password"
    )
    role: Optional[str] = Field(None, description="Role name: Admin | Staff | Enterprise Division")
    roles_id: Optional[int] = Field(None, description="Numeric role id")

class RoleOut(BaseModel):
    roles_id: int
    role_name: str

# ---- Orders ----
class ORPayload(BaseModel):
    OR_number: str

# ---- Sales ----
class SaleItemIn(BaseModel):
    item_id: int
    quantity: int

class SaleCreateIn(BaseModel):
    user_id: int
    customer_name: str
    OR_number: Optional[str] = None
    student_id: Optional[str] = None
    course: Optional[str] = None
    items: List[SaleItemIn]