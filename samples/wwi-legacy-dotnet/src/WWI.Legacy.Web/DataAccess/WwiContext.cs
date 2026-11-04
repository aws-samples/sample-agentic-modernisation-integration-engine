using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;
using System.Data.Entity;
using System.Data.Entity.Spatial;
using System.Linq;

namespace WWI.Legacy.Web.DataAccess
{
    [Table("Cities", Schema = "Application")]
    public class City
    {
        [Key]
        public int CityID { get; set; }
        public string CityName { get; set; }
        public int StateProvinceID { get; set; }
        public DbGeography Location { get; set; }
        public long? LatestRecordedPopulation { get; set; }
        public int LastEditedBy { get; set; }
        [Column(TypeName = "datetime2")]
        public DateTime ValidFrom { get; set; }
    }

    public class StockItem
    {
        public int StockItemID { get; set; }
        public string StockItemName { get; set; }
        public decimal UnitPrice { get; set; }
        [Timestamp]
        public byte[] RowVersion { get; set; }
    }

    /// <summary>Entity Framework 6 code-first context over WideWorldImporters.</summary>
    public class WwiContext : DbContext
    {
        public WwiContext() : base("name=WideWorldImporters") { }

        public DbSet<City> Cities { get; set; }
        public DbSet<StockItem> StockItems { get; set; }

        protected override void OnModelCreating(DbModelBuilder modelBuilder)
        {
            modelBuilder.HasDefaultSchema("Warehouse");
            modelBuilder.Entity<StockItem>().ToTable("StockItems", "Warehouse");
            modelBuilder.Entity<StockItem>().Property(s => s.UnitPrice).HasColumnType("money");
        }

        public List<City> CitiesNear(double latitude, double longitude, int meters)
        {
            var point = DbGeography.FromText($"POINT({longitude} {latitude})", 4326);
            return Cities.Where(c => c.Location.Distance(point) < meters).ToList();
        }

        public List<string> StockItemNamesByTag(string tag)
        {
            return Database.SqlQuery<string>(
                "SELECT si.StockItemName FROM Warehouse.StockItems AS si " +
                "CROSS APPLY OPENJSON(si.Tags) AS t WHERE t.value = @p0",
                tag).ToList();
        }

        public int PurgeOldColdRoomReadings(int days)
        {
            return Database.ExecuteSqlCommand(
                "DELETE TOP (10000) FROM Warehouse.ColdRoomTemperatures_Archive " +
                "WHERE ValidTo < DATEADD(day, -@p0, SYSUTCDATETIME())", days);
        }
    }
}
