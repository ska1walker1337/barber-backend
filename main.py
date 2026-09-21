from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import select, and_, or_
from pydantic import BaseModel
from datetime import date, time, datetime, timedelta
from typing import List, Optional
import os
from dotenv import load_dotenv

load_dotenv()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# Models
class Service(Base):
    __tablename__ = "services"
    
    id = Base.Column(Base.Integer, primary_key=True)
    name = Base.Column(Base.String(100), nullable=False)
    price = Base.Column(Base.Integer, nullable=False)
    duration_minutes = Base.Column(Base.Integer, nullable=False)
    description = Base.Column(Base.Text)
    icon = Base.Column(Base.String(10))

class Master(Base):
    __tablename__ = "masters"
    
    id = Base.Column(Base.Integer, primary_key=True)
    name = Base.Column(Base.String(100), nullable=False)
    photo_url = Base.Column(Base.String(500))
    rating = Base.Column(Base.Float, default=0.0)
    experience_years = Base.Column(Base.Integer, default=0)
    specialties = Base.Column(Base.Text)  # JSON array as string
    work_start = Base.Column(Base.Time, default=time(10, 0))
    work_end = Base.Column(Base.Time, default=time(20, 0))

class Booking(Base):
    __tablename__ = "bookings"
    
    id = Base.Column(Base.Integer, primary_key=True)
    service_id = Base.Column(Base.Integer, Base.ForeignKey("services.id"))
    master_id = Base.Column(Base.Integer, Base.ForeignKey("masters.id"))
    telegram_user_id = Base.Column(Base.BigInteger, nullable=False)
    client_name = Base.Column(Base.String(200))
    date = Base.Column(Base.Date, nullable=False)
    start_time = Base.Column(Base.Time, nullable=False)
    end_time = Base.Column(Base.Time, nullable=False)
    status = Base.Column(Base.String(20), default="pending")
    created_at = Base.Column(Base.DateTime, default=datetime.utcnow)

# Pydantic schemas
class ServiceResponse(BaseModel):
    id: int
    name: str
    price: int
    duration_minutes: int
    description: Optional[str]
    icon: Optional[str]
    
    class Config:
        from_attributes = True

class MasterResponse(BaseModel):
    id: int
    name: str
    photo_url: Optional[str]
    rating: float
    experience_years: int
    specialties: Optional[str]
    
    class Config:
        from_attributes = True

class TimeSlot(BaseModel):
    time: str
    available: bool

class BookingCreate(BaseModel):
    service_id: int
    master_id: int
    date: str
    time: str
    telegram_user_id: int
    client_name: str

class BookingResponse(BaseModel):
    id: int
    service_id: int
    master_id: int
    telegram_user_id: int
    client_name: str
    date: str
    start_time: str
    end_time: str
    status: str
    
    class Config:
        from_attributes = True

# FastAPI app
app = FastAPI(title="Barber Shop API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

# Endpoints
@app.get("/")
async def root():
    return {"message": "Barber Shop API is running"}

@app.get("/api/services", response_model=List[ServiceResponse])
async def get_services(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Service))
    services = result.scalars().all()
    return services

@app.get("/api/masters", response_model=List[MasterResponse])
async def get_masters(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Master))
    masters = result.scalars().all()
    return masters

@app.get("/api/slots", response_model=List[TimeSlot])
async def get_slots(
    master_id: int,
    service_id: int,
    date: str,
    db: AsyncSession = Depends(get_db)
):
    # Get service duration
    service = await db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Get master work hours
    master = await db.get(Master, master_id)
    if not master:
        raise HTTPException(status_code=404, detail="Master not found")
    
    # Generate all time slots
    slots_needed = (service.duration_minutes + 29) // 30  # Round up
    buffer_slots = 1
    
    all_times = []
    current_hour = master.work_start.hour
    current_minute = master.work_start.minute
    
    while current_hour < master.work_end.hour or (current_hour == master.work_end.hour and current_minute < master.work_end.minute):
        time_str = f"{current_hour:02d}:{current_minute:02d}"
        all_times.append(time_str)
        
        current_minute += 30
        if current_minute >= 60:
            current_minute = 0
            current_hour += 1
    
    # Get existing bookings for this master and date
    booking_date = datetime.strptime(date, "%Y-%m-%d").date()
    result = await db.execute(
        select(Booking).where(
            and_(
                Booking.master_id == master_id,
                Booking.date == booking_date,
                Booking.status.in_(["pending", "confirmed"])
            )
        )
    )
    bookings = result.scalars().all()
    
    # Mark occupied slots
    occupied = set()
    for booking in bookings:
        booking_idx = all_times.index(booking.start_time.strftime("%H:%M"))
        
        # Calculate booking duration in slots
        booking_duration_slots = 2  # Default 60 min
        if booking.end_time and booking.start_time:
            start_minutes = booking.start_time.hour * 60 + booking.start_time.minute
            end_minutes = booking.end_time.hour * 60 + booking.end_time.minute
            duration_minutes = end_minutes - start_minutes
            booking_duration_slots = (duration_minutes + 29) // 30
        
        # Mark slots as occupied
        for i in range(booking_duration_slots):
            if booking_idx + i < len(all_times):
                occupied.add(all_times[booking_idx + i])
        
        # Mark buffer slots
        for i in range(buffer_slots):
            if booking_idx + booking_duration_slots + i < len(all_times):
                occupied.add(all_times[booking_idx + booking_duration_slots + i])
    
    # Check availability for each slot
    slots = []
    for time_str in all_times:
        start_idx = all_times.index(time_str)
        can_fit = True
        
        total_needed = slots_needed + buffer_slots
        for i in range(total_needed):
            check_idx = start_idx + i
            if check_idx >= len(all_times) or all_times[check_idx] in occupied:
                can_fit = False
                break
        
        slots.append(TimeSlot(
            time=time_str,
            available=can_fit and time_str not in occupied
        ))
    
    return slots

@app.post("/api/bookings", response_model=BookingResponse)
async def create_booking(
    booking_data: BookingCreate,
    db: AsyncSession = Depends(get_db)
):
    # Validate service
    service = await db.get(Service, booking_data.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Validate master
    master = await db.get(Master, booking_data.master_id)
    if not master:
        raise HTTPException(status_code=404, detail="Master not found")
    
    # Parse date and time
    booking_date = datetime.strptime(booking_data.date, "%Y-%m-%d").date()
    start_time = datetime.strptime(booking_data.time, "%H:%M").time()
    
    # Calculate end time
    start_datetime = datetime.combine(booking_date, start_time)
    end_datetime = start_datetime + timedelta(minutes=service.duration_minutes)
    end_time = end_datetime.time()
    
    # Check for conflicts
    result = await db.execute(
        select(Booking).where(
            and_(
                Booking.master_id == booking_data.master_id,
                Booking.date == booking_date,
                Booking.status.in_(["pending", "confirmed"]),
                or_(
                    and_(
                        Booking.start_time <= start_time,
                        Booking.end_time > start_time
                    ),
                    and_(
                        Booking.start_time < end_time,
                        Booking.end_time >= end_time
                    ),
                    and_(
                        Booking.start_time >= start_time,
                        Booking.end_time <= end_time
                    )
                )
            )
        )
    )
    conflicts = result.scalars().all()
    
    if conflicts:
        raise HTTPException(status_code=409, detail="Time slot already booked")
    
    # Create booking
    new_booking = Booking(
        service_id=booking_data.service_id,
        master_id=booking_data.master_id,
        telegram_user_id=booking_data.telegram_user_id,
        client_name=booking_data.client_name,
        date=booking_date,
        start_time=start_time,
        end_time=end_time,
        status="pending"
    )
    
    db.add(new_booking)
    await db.commit()
    await db.refresh(new_booking)
    
    return BookingResponse(
        id=new_booking.id,
        service_id=new_booking.service_id,
        master_id=new_booking.master_id,
        telegram_user_id=new_booking.telegram_user_id,
        client_name=new_booking.client_name,
        date=new_booking.date.isoformat(),
        start_time=new_booking.start_time.strftime("%H:%M"),
        end_time=new_booking.end_time.strftime("%H:%M"),
        status=new_booking.status
    )

@app.get("/api/bookings/me", response_model=List[BookingResponse])
async def get_my_bookings(
    telegram_user_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Booking).where(
            Booking.telegram_user_id == telegram_user_id
        ).order_by(Booking.date.desc(), Booking.start_time.desc())
    )
    bookings = result.scalars().all()
    
    return [
        BookingResponse(
            id=b.id,
            service_id=b.service_id,
            master_id=b.master_id,
            telegram_user_id=b.telegram_user_id,
            client_name=b.client_name,
            date=b.date.isoformat(),
            start_time=b.start_time.strftime("%H:%M"),
            end_time=b.end_time.strftime("%H:%M"),
            status=b.status
        )
        for b in bookings
    ]

# Startup event - create tables and seed data
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Seed data
        result = await conn.execute(select(Service))
        if not result.scalars().first():
            # Add services
            services = [
                Service(id=1, name="Мужская стрижка", price=1500, duration_minutes=60, description="Классическая или модельная стрижка с укладкой", icon="✂️"),
                Service(id=2, name="Моделирование бороды", price=800, duration_minutes=30, description="Стрижка и оформление бороды, опасная бритва", icon="🪒"),
                Service(id=3, name="Комплекс Брутальный", price=2000, duration_minutes=90, description="Стрижка + борода + уход + укладка", icon="👑"),
                Service(id=4, name="Камуфляж седины", price=1200, duration_minutes=45, description="Камуфляж седины для мужчин", icon="🧔"),
            ]
            for s in services:
                await conn.execute(
                    Service.__table__.insert().values(
                        id=s.id, name=s.name, price=s.price,
                        duration_minutes=s.duration_minutes,
                        description=s.description, icon=s.icon
                    )
                )
            
            # Add masters
            masters = [
                Master(id=1, name="Алекс", rating=4.9, experience_years=7, specialties="Фейды,Классика,Бороды"),
                Master(id=2, name="Иван", rating=4.8, experience_years=5, specialties="Креатив,Длинные волосы,Камуфляж"),
                Master(id=3, name="Виктор", rating=5.0, experience_years=2, specialties="Фейд,Окрашивание,Запрос стрижки"),
            ]
            for m in masters:
                await conn.execute(
                    Master.__table__.insert().values(
                        id=m.id, name=m.name, rating=m.rating,
                        experience_years=m.experience_years,
                        specialties=m.specialties
                    )
                )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)