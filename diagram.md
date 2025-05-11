```mermaid
classDiagram
    %%—— Purchase Orders ——————————————————————————————%%
    class RFT_PurchaseOrder {
      + POID: Integer
      + PONumber: String
      + Supplier: String
      + Brand:    String
      + PODate:   Date
      + LCStatus: String
      + LCDate:   Date
    }

    class RFT_PurchaseOrderLine {
      + POLineID: Integer
      + Article:  String
      + Qty:      Integer
      + BalanceQty: Integer
      + CategoryMappingID: Integer
    }

    RFT_PurchaseOrder "1" --> "0..*" RFT_PurchaseOrderLine : contains

    %%—— Categories Mapping ——————————————————————————%%
    class RFT_CategoriesMappingMain {
      + ID:      Integer
      + Brand:   String
      + CatCode: String
      + CatName: String
    }
    RFT_CategoriesMappingMain "1" <-- "0..*" RFT_PurchaseOrderLine : CategoryMappingID

    %%—— Shipments ——————————————————————————————————————%%
    class RFT_Shipment {
      + ShipmentID:     Integer
      + ShipmentNumber: String
      + ShippingLine:   String
      + POD:            String
      + DestinationCountry: String
    }

    class RFT_ShipmentPOLine {
      + ShipmentPOLineID: Integer
      + QtyShipped:       Integer
      + ECCDate:          DateTime
    }

    RFT_Shipment      "1" --> "0..*" RFT_ShipmentPOLine : contains
    RFT_PurchaseOrderLine "1" --> "0..*" RFT_ShipmentPOLine : ships

    %%—— Containers —————————————————————————————————————%%
    class RFT_Container {
      + ContainerID:     Integer
      + ContainerNumber: String
      + LoadingPort:     String
      + PortOfArrival:   String
    }

    class RFT_ContainerLine {
      + ContainerLineID:  Integer
      + QtyInContainer:   Integer
    }

    RFT_Shipment          "1" --> "0..*" RFT_Container : loads
    RFT_ShipmentPOLine    "1" --> "0..*" RFT_ContainerLine : fills
    RFT_Container         "1" --> "0..*" RFT_ContainerLine : holds

    %%—— Status Management —————————————————————————————%%
    class RFT_StatusManagement {
      + ID:         Integer
      + Level:      String
      + StatusName: String
    }

    class RFT_StatusHistory {
      + StatusHistoryID: Integer
      + EntityType:      String
      + EntityID:        Integer
      + StatusDate:      DateTime
    }

    RFT_StatusManagement "1" --> "0..*" RFT_StatusHistory : defines
    %% each history row points to an EntityType/EntityID

    %%—— Interval Config & Dropdowns —————————————————————%%
    class RFT_IntervalConfig {
      + ID:           Integer
      + IntervalName: String
      + StartField:   String
      + EndField:     String
    }

    class RFT_IncoTerms {
      + ID:          Integer
      + Code:        String
      + Description: String
    }
    class RFT_ModeOfTransport {
      + ID:   Integer
      + Mode: String
    }
    class RFT_CustomAgents {
      + ID:         Integer
      + AgentName:  String
    }
    class RFT_OriginPorts {
      + ID:       Integer
      + PortName: String
    }
    class RFT_ShipingLines {
      + ID:               Integer
      + ShipingLineName:  String
    }
    class RFT_DestinationPorts {
      + ID:       Integer
      + PortName: String
    }

    %%—— Freight Tracking View (denormalized) —————————————————————%%
    class FreightTrackingView {
      + POID:             Integer
      + ShipmentID:       Integer
      + ContainerID:      Integer
      + Article:          String
      + Qty:              Integer
      + QtyShipped:       Integer
      + ATDOrigin:        DateTime
      + ATAWH:            DateTime
      + ShipmentLevelStatus: String
    }
    %% This view joins the three line tables for easy querying:
    FreightTrackingView "0..1" <-- "1" RFT_PurchaseOrderLine : POLineID
    FreightTrackingView "0..1" <-- "1" RFT_ShipmentPOLine   : ShipmentPOLineID
    FreightTrackingView "0..1" <-- "1" RFT_ContainerLine    : ContainerLineID
