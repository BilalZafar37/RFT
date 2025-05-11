from sqlalchemy import ( create_engine, inspect, case,
                        String, literal,cast,
                        Column, Integer, String, Numeric, 
                        BigInteger, ForeignKey, Text, DateTime,
                        Float, Date, bindparam, func, desc, and_, or_, 
                        bindparam, select, distinct, literal, literal_column,
                        text, PrimaryKeyConstraint, extract, delete)
# from sqlalchemy import coalesce
import urllib.parse
from sqlalchemy.orm import declarative_base, aliased
from sqlalchemy.orm import relationship, backref
from datetime import datetime
from sqlalchemy.orm import foreign, joinedload
from sqlalchemy.orm import sessionmaker, scoped_session



def db_connection():
    DRIVER = "ODBC Driver 17 for SQL Server"
    USERNAME = "mec_wh"
    PSSWD = "mecwh@(2023)"
    SERVERNAME = "172.16.2.96"
    DATABASE = "MEC_WH_inventory_Management_DEV"
    encoded_password = urllib.parse.quote_plus(PSSWD)
    connection_string = f"mssql+pyodbc://{USERNAME}:{encoded_password}@{SERVERNAME}/{DATABASE}?driver={DRIVER}"
    # print(connection_string)
    engine = create_engine(connection_string)

    return engine

engine = db_connection()

# initialize extensions
Session = sessionmaker(bind=engine)
model = Session()

# Base class for declarative models
Base = declarative_base()


class Users(Base):
    __tablename__ = 'Users'

    # Defining the columns
    UserID      = Column(Integer, primary_key=True, autoincrement=True)
    Username    = Column(String(100), nullable=False)
    Password    = Column(String(100), nullable=False)
    Role        = Column(String(100), nullable=False)
    Email       = Column(String(100), nullable=False)
    Company     = Column(String(100), nullable=True)
    Address     = Column(String(200), nullable=True)
    Department  = Column(String(100), nullable=False)
    IP          = Column(String(20), nullable=True)
    DPR         = Column(Integer, nullable=True)  # Modify if it's not an integer
    INV         = Column(Integer, nullable=True)  # Modify if it's not an integer
    Fullname    = Column(String(200), nullable=True)

class Sites(Base):
    __tablename__ = 'RTOS_Sites'

    SiteID = Column(Integer, primary_key=True, autoincrement=True)
    SiteName = Column(String(100), nullable=False)

class UserSites(Base):
    __tablename__ = 'RTOS_UserSites'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    UserID = Column(Integer, ForeignKey('Users.UserID'))  # Correct table name
    SiteID = Column(Integer, ForeignKey('RTOS_Sites.SiteID'))  # Correct table name

    user = relationship('Users', backref='site_access')
    site = relationship('Sites', backref='user_access')  # Reference the Sites class, not the table name
    
class Brands(Base):
    __tablename__ = 'RTOS_Brands'

    BrandID = Column(Integer, primary_key=True, autoincrement=True)
    BrandName = Column(String(100), nullable=False)

class UserBrands(Base):
    __tablename__ = 'RTOS_UserBrands'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    UserID = Column(Integer, ForeignKey('Users.UserID'))  # Correct table name
    BrandID = Column(Integer, ForeignKey('RTOS_Brands.BrandID'))  # Correct table name

    user = relationship('Users', backref='brand_access')
    brand = relationship('Brands', backref='user_access')  # Reference the Sites class, not the table name



class RFT_PurchaseOrderUpload(Base):
    __tablename__ = 'RFT_PurchaseOrderUpload'

    UploadID            = Column(Integer, primary_key=True, autoincrement=True)
    UploadBatch         = Column(String(50), nullable=False)
    PurchaseOrder       = Column(String(50))
    Item                = Column(String(50))
    Type                = Column(String(50))
    PGR                 = Column(String(50))
    VendorSupplyingSite = Column(String(255))
    Article             = Column(String(255))
    ShortText           = Column(String(255))
    MdseCat             = Column(String(100))
    Site                = Column(String(50))
    SLoc                = Column(String(50))
    DocDate             = Column(DateTime)
    Quantity            = Column(Integer)
    Netprice            = Column(Numeric(18,2))
    QtyToBeDelivered    = Column(Integer)
    ValueToBeDelivered  = Column(Numeric(18,2))
    UploadedAt          = Column(DateTime, server_default=text('GETDATE()'))
    UploadedBy          = Column(String(100))


class RFT_PurchaseOrder(Base):
    __tablename__ = 'RFT_PurchaseOrder'
    
    POID = Column(Integer, primary_key=True, autoincrement=True)
    PONumber = Column(String(50), unique=True)
    Supplier = Column(String(100))
    Brand = Column(String(100))
    PODate = Column(Date)
    LCStatus = Column(String(5))
    LCNumber = Column(String(50))
    LCDate = Column(Date)
    ModeOfTransport = Column(String(50))
    INCOTerms = Column(String(50))
    CreatedDate = Column(DateTime, server_default=text('GETDATE()'))
    CreatedBy = Column(String(50))
    LastUpdated = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    LastUpdatedBy = Column(String(50))
    
    # ← add this so PO can see its lines
    order_lines = relationship(
      "RFT_PurchaseOrderLine",
      back_populates="purchase_order",
      cascade="all, delete-orphan"
    )

class RFT_PurchaseOrderLine(Base):
    __tablename__ = 'RFT_PurchaseOrderLine'
    
    POLineID            = Column(Integer, primary_key=True, autoincrement=True)
    POID                = Column(Integer, ForeignKey("RFT_PurchaseOrder.POID"), nullable=False)
    SapItemLine         = Column(String(50))
    Article             = Column(String(100))
    Qty                 = Column(Integer)
    BalanceQty          = Column(Integer)
    TotalValue          = Column(Numeric(18, 2))
    LastUpdated         = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    LastUpdatedBy       = Column(String(50))
    CategoryMappingID   = Column(Integer, ForeignKey("RFT_CategoriesMappingMain.ID"), nullable=True) # column linking to RFT_CategoriesMappingMain
    CategoryMapping     = relationship("RFT_CategoriesMappingMain", backref="order_lines")
    
    # ← link back up to purchase order
    purchase_order = relationship(
      "RFT_PurchaseOrder",
      back_populates="order_lines"
    )
    
    shipment_lines = relationship(
      "RFT_ShipmentPOLine",
      back_populates="po_line",
      cascade="all, delete-orphan"
    )

class RFT_Shipment(Base):
    __tablename__ = 'RFT_Shipment'
    
    ShipmentID = Column(Integer, primary_key=True, autoincrement=True)
    ShipmentNumber = Column(String(50), unique=True)
    Categories = Column(String(100))
    BillOfLading = Column(String(100))
    ShippingLine = Column(String(100))
    BLNumber = Column(String(50))
    
    POD = Column(String(100))
    DestinationCountry = Column(String(100))
    OriginPort = Column(String(100))
    OriginCountry = Column(String(100))
    
    FreightCost = Column(Numeric(18, 2))
    SaberSADDAD = Column(Numeric(18, 2))
    CustomDuties = Column(Integer)
    DemurrageCharges = Column(Numeric(18, 2))
    Penalties = Column(Numeric(18, 2))
    OtherCharges = Column(Numeric(18, 2))
    YardCharges =  Column(Numeric(18, 2))
    DO_Port_Charges = Column(Numeric(18, 2))
    ValueDecByCC = Column(Numeric(18, 2))
    ClearanceTransportCharges = Column(Numeric(18, 2))
    CostRemarks = Column(String(100))
    
    CreatedDate = Column(DateTime, server_default=text('GETDATE()'))
    CreatedBy = Column(String(50))
    LastUpdated = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    LastUpdatedBy = Column(String(50))
    
    CCAgent = Column(String(100))
    # Estimated times on shipment level
    ECCDate = Column(DateTime)
    ETAWH = Column(DateTime)
    ETADestination = Column(DateTime)
    ETAOrigin = Column(DateTime)
    ETDOrigin = Column(DateTime)
    
    BiyanNumber = Column(String(100))
    SADDADNumber = Column(String(100))
    
    # ← add these three ↓
    po_lines   = relationship(
      "RFT_ShipmentPOLine",
      back_populates="shipment",
      cascade="all, delete-orphan"
    )
    containers = relationship(
      "RFT_Container",
      back_populates="shipment",
      cascade="all, delete-orphan"
    )
    invoices   = relationship(
      "RFT_Invoices",
      back_populates="shipment",
      cascade="all, delete-orphan"
    )
    


class RFT_ShipmentPOLine(Base):
    __tablename__ = 'RFT_ShipmentPOLine'
    
    ShipmentPOLineID = Column(Integer, primary_key=True, autoincrement=True)
    ShipmentID = Column(Integer, ForeignKey("RFT_Shipment.ShipmentID"), nullable=False)
    POLineID = Column(Integer, ForeignKey("RFT_PurchaseOrderLine.POLineID"), nullable=False)
    QtyShipped = Column(Integer, nullable=False)
    ECCDate = Column(DateTime)  # Expected Customs Clearance date
    LastUpdated = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    LastUpdatedBy = Column(String(50))
    
    # …
    shipment         = relationship("RFT_Shipment",      back_populates="po_lines")
    po_line          = relationship("RFT_PurchaseOrderLine", back_populates="shipment_lines")
    
    # ← add this so you can go from a POLine -> its container‐lines
    container_lines = relationship(
      "RFT_ContainerLine",
      back_populates="shipment_po_line",
      cascade="all, delete-orphan"
    )

class RFT_Container(Base):
    __tablename__ = 'RFT_Container'
    
    ContainerID = Column(Integer, primary_key=True, autoincrement=True)
    ContainerNumber = Column(String(50), nullable=False)
    ShipmentID = Column(Integer, ForeignKey("RFT_Shipment.ShipmentID"), nullable=False)
    ContainerType = Column(String(50))
    # LoadingPort = Column(String(100))
    # PortOfArrival = Column(String(100))
    # ATAs
    CCDate = Column(DateTime)
    ATAOrigin = Column(DateTime)
    ATDOrigin = Column(DateTime)
    ATADP = Column(DateTime)
    ATDDPort = Column(DateTime)
    ATAWH = Column(DateTime)
    
    YardInDate = Column(DateTime)
    YardOutDate = Column(DateTime)
    
    ContainerRemarks = Column(String(100))

    UpdatedAt = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    UpdatedBy = Column(String(50))
    # 
    shipment        = relationship("RFT_Shipment", back_populates="containers")
    lines           = relationship(
      "RFT_ContainerLine",
      back_populates="container",
      cascade="all, delete-orphan"
    )

class RFT_ContainerLine(Base):
    __tablename__ = 'RFT_ContainerLine'
    
    ContainerLineID = Column(Integer, primary_key=True, autoincrement=True)
    ContainerID = Column(Integer, ForeignKey("RFT_Container.ContainerID"), nullable=False)
    ShipmentPOLineID = Column(Integer, ForeignKey("RFT_ShipmentPOLine.ShipmentPOLineID"), nullable=False)
    QtyInContainer = Column(Integer, nullable=False)
    LastUpdated = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    LastUpdatedBy = Column(String(50))
    
    container        = relationship("RFT_Container",     back_populates="lines")
    shipment_po_line = relationship("RFT_ShipmentPOLine")

class RFT_Invoices(Base):
    __tablename__ = 'RFT_Invoices'

    InvoiceID     = Column(Integer, primary_key=True, autoincrement=True)
    ShipmentID    = Column(Integer, ForeignKey('RFT_Shipment.ShipmentID', ondelete='CASCADE'), nullable=False)
    InvoiceNumber = Column(String(50),  nullable=False)
    InvoiceValue  = Column(Numeric(18,2), nullable=False)
    CreatedBy     = Column(String(50),  nullable=True)
    CreatedAt     = Column(DateTime, server_default=text('GETDATE()'), nullable=False)
    UpdatedBy     = Column(String(50),  nullable=True)
    UpdatedAt     = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow, nullable=False)

    # relationship back to shipment
    shipment      = relationship('RFT_Shipment', back_populates='invoices')
    
####################################
####### SATATUS MANAGEMENT #########
####################################
class RFT_StatusManagement(Base):
    __tablename__ = 'RFT_StatusManagement'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    Level = Column(String(50), nullable=False)
    StatusName = Column(String(100), nullable=False)
    UpdatedAt = Column(DateTime, server_default=text('GETDATE()'), onupdate=datetime.utcnow)
    UpdatedBy = Column(String(50))
    CreatedBy = Column(String(50))

class RFT_StatusHistory(Base):
    __tablename__ = 'RFT_StatusHistory'
    
    StatusHistoryID = Column(Integer, primary_key=True, autoincrement=True)
    EntityType = Column(String(50))   # e.g. 'PurchaseOrder', 'Shipment', 'Container'
    EntityID = Column(Integer, nullable=False)
    Status = Column(String(50))
    StatusDate = Column(DateTime, nullable=False)
    UpdatedBy = Column(String(50))
    Comments = Column(String(250))


####################################
####### Categories Mapping #########
####################################
class RFT_CategoriesMappingMain(Base):
    __tablename__ = "RFT_CategoriesMappingMain"
    
    ID = Column(Integer, primary_key=True, autoincrement=True)
    Brand = Column(String(100), nullable=False)
    CatCode = Column(String(50), nullable=False)
    CatName = Column(String(100), nullable=False)
    CatDesc = Column('CatDesc', String(255), key = 'CATDesc')
    SubCat = Column(String(100))
    UpdatedBy = Column(String(50))
    UpdatedAt = Column(DateTime, default=datetime.utcnow)

class RFT_CategoriesMappingSDA(Base):
    __tablename__ = "RFT_CategoriesMappingSDA"
    
    ID = Column(Integer, primary_key=True, autoincrement=True)
    SDANames = Column(String(100))
    UpdatedBy = Column(String(50))
    UpdatedAt = Column(DateTime, default=datetime.utcnow)


# LC chart intervals and names
class RFT_IntervalConfig(Base):
    __tablename__ = 'RFT_IntervalConfig'
    ID            = Column(Integer, primary_key=True, autoincrement=True)
    IntervalName  = Column(String(100), nullable=False)
    StartField    = Column(String(50),  nullable=False)
    EndField      = Column(String(50),  nullable=False)
    CreatedBy     = Column(String(50))
    CreatedAt     = Column(DateTime, server_default=text('GETDATE()'))
    UpdatedBy     = Column(String(50))
    UpdatedAt     = Column(DateTime, server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

# Column labels
class RFT_FieldLabels(Base):
    __tablename__ = 'RFT_FieldLabels'

    ID         = Column('ID',           Integer,   primary_key=True, autoincrement=True)
    TableName = Column('TableName',    String(128), nullable=False)
    FieldName = Column('FieldName',    String(128), nullable=False)
    Label      = Column('Label',        String(256), nullable=False)
    CreatedBy = Column('CreatedBy',    String(50),  nullable=True)
    CreatedAt = Column('CreatedAt',    DateTime,    server_default=text('GETDATE()'))
    UpdatedBy = Column('UpdatedBy',    String(50),  nullable=True)
    UpdatedAt = Column('UpdatedAt',    DateTime,    
                           server_default=text('GETDATE()'),
                           server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_FieldLabels {self.table_name}.{self.field_name} → {self.label!r}>"

# settings
class RFT_Settings(Base):
    __tablename__ = 'RFT_Settings'
    SettingID   = Column(Integer, primary_key=True, autoincrement=True)
    UserID      = Column(String(50), nullable=True)
    SettingKey  = Column(String(100), nullable=False, unique=True)
    SettingValue= Column(Text, nullable=False)


####################################
####### DROPDOWNS ##################
####################################
class RFT_IncoTerms(Base):
    __tablename__ = 'RFT_IncoTerms'

    id          = Column('ID',          Integer,   primary_key=True, autoincrement=True)
    code        = Column('Code',        String(10), nullable=False, unique=True)
    description = Column('Description', String(100), nullable=False)
    CreatedBy   = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt   = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy   = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt   = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_IncoTerms {self.code}>"

class RFT_ModeOfTransport(Base):
    __tablename__ = 'RFT_ModeOfTransport'

    id         = Column('ID',   Integer,   primary_key=True, autoincrement=True)
    mode       = Column('Mode', String(50), nullable=False, unique=True)
    CreatedBy  = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt  = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy  = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt  = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_ModeOfTransport {self.mode}>"

class RFT_CustomAgents(Base):
    __tablename__ = 'RFT_CustomAgents'

    id         = Column('ID',        Integer,   primary_key=True, autoincrement=True)
    agent_name = Column('AgentName', String(100), nullable=False)
    CreatedBy  = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt  = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy  = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt  = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_CustomAgents {self.agent_name}>"

class RFT_OriginPorts(Base):
    __tablename__ = 'RFT_OriginPorts'

    id         = Column('ID',       Integer,   primary_key=True, autoincrement=True)
    port_name  = Column('PortName', String(150), nullable=False)
    CreatedBy  = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt  = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy  = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt  = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_OriginPorts {self.port_name}>"

class RFT_ShipingLines(Base):
    __tablename__ = 'RFT_ShipingLines'

    id               = Column('ID',               Integer,   primary_key=True, autoincrement=True)
    ShipingLineName  = Column('ShipingLineName',  String(255), nullable=False)
    CreatedBy        = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt        = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy        = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt        = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()')) 

class RFT_DestinationPorts(Base):
    __tablename__ = 'RFT_DestinationPorts'

    id         = Column('ID',       Integer,   primary_key=True, autoincrement=True)
    port_name  = Column('PortName', String(150), nullable=False)
    CreatedBy  = Column('CreatedBy',   String(50), nullable=True)
    CreatedAt  = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy  = Column('UpdatedBy',   String(50), nullable=True)
    UpdatedAt  = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_DestinationPorts {self.port_name}>"

class RFT_BrandTypes(Base):
    __tablename__ = 'RFT_BrandTypes'

    ID         = Column('ID',       Integer,   primary_key=True, autoincrement=True)
    BrandType  = Column('BrandType', String(100), nullable=False)
    BrandName  = Column('BrandName',   String(100), nullable=False)
    # CreatedAt  = Column('CreatedAt',   DateTime,   server_default=text('GETDATE()'))
    UpdatedBy  = Column('UpdatedBy',   String(100), nullable=True)
    UpdatedAt  = Column('UpdatedAt',   DateTime,   server_default=text('GETDATE()'), server_onupdate=text('GETDATE()'))

    def __repr__(self):
        return f"<RFT_DestinationPorts {self.port_name}>"


####################################
####################################
####################################



class FreightTrackingView(Base):
    __tablename__ = 'vw_AllFreightData'
    
    POID            = Column(Integer, primary_key=True)
    POLineID        = Column(Integer)
    ShipmentID      = Column(Integer)
    ShipmentPOLineID= Column(Integer)
    ContainerID     = Column(Integer)
    ContainerLineID = Column(Integer)
    
    
    PONumber        = Column(String(50))
    Supplier        = Column(String(100))
    Brand           = Column(String(100))
    PODate          = Column(Date)
    LCStatus        = Column(String(5))
    LCNumber        = Column(String(50))
    LCDate          = Column(Date)
    ModeOfTransport = Column(String(50))
    INCOTerms       = Column(String(50))
    POCreatedDate   = Column(Date)
    
    
    Article     = Column(String(100))
    CatName     = Column(String(100))
    CATDesc     = Column(String(100))
    SubCat      = Column(String(100))
    Qty         = Column(Integer)
    BalanceQty  = Column(Integer)
    TotalValue  = Column(Numeric(18, 2))
    
    
    ShipmentNumber      = Column(String(50))
    QtyShipped          = Column(Integer)
    Categories          = Column(String(100))
    BillOfLading        = Column(String(100))
    ShippingLine        = Column(String(100))
    POD                 = Column(String(100))
    DestinationCountry  = Column(String(100))
    BLNumber            = Column(String(500))
    CCAgent             = Column(String(100))
    InvoiceNumber       = Column(String(50))
    
    # Estimated times on shipment level
    ECCDate         = Column(DateTime)
    ETAWH           = Column(DateTime)
    ETADestination  = Column(DateTime)
    ETAOrigin       = Column(DateTime)
    ETDOrigin       = Column(DateTime)
    
    #cost and INVOICE
    InvoiceValue        = Column(String(500))
    FreightCost         = Column(Numeric(18, 2))
    SaberSADDAD         = Column(Numeric(18, 2))
    CustomDuties        = Column(Numeric(18, 2))
    DemurrageCharges    = Column(Numeric(18, 2))
    Penalties           = Column(Numeric(18, 2))
    OtherCharges        = Column(Numeric(18, 2))
    YardCharges         = Column(Numeric(18, 2))
    DO_Port_Charges     = Column(Numeric(18, 2))
    ValueDecByCC        = Column(Numeric(18, 2))
    ClearanceTransportCharges = Column(Numeric(18, 2))
    
    CostRemarks = Column(String(100))
    
    BiyanNumber     = Column(String(100))
    SADDADNumber    = Column(String(100))
    
    ContainerNumber = Column(String(50))
    ContainerType   = Column(String(50))
    LoadingPort     = Column(String(100))
    PortOfArrival   = Column(String(100))
   
    CCDate      = Column(DateTime)
    ATAOrigin   = Column(DateTime)
    ATDOrigin   = Column(DateTime)
    ATADP       = Column(DateTime)
    ATDDPort    = Column(DateTime)
    ATAWH       = Column(DateTime)
    YardInDate  = Column(DateTime)
    YardOutDate = Column(DateTime)
    
    
    QtyInContainer      = Column(Integer)
    ContainerRemarks    = Column(String(100))
    
    POLevelStatus       = Column(String(100))
    ShipmentLevelStatus = Column(String(100))
    ContainerLevelStatus= Column(String(100))
    
    
    
