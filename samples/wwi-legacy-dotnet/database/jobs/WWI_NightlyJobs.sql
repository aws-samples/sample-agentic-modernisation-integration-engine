-- SQL Server Agent jobs for the WWI back office. Scripted from SSMS ("Script Job as > CREATE To").
USE [msdb]
GO

BEGIN TRANSACTION
DECLARE @ReturnCode INT
SELECT @ReturnCode = 0

DECLARE @jobId BINARY(16)
EXEC @ReturnCode = msdb.dbo.sp_add_job @job_name = N'WWI - Nightly Daily Process',
        @enabled = 1,
        @description = N'Runs DataLoadSimulation.DailyProcessToCreateHistory for the previous day',
        @owner_login_name = N'sa',
        @job_id = @jobId OUTPUT
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

EXEC @ReturnCode = msdb.dbo.sp_add_jobstep @job_id = @jobId,
        @step_name = N'Create history',
        @subsystem = N'TSQL',
        @database_name = N'WideWorldImporters',
        @command = N'EXEC DataLoadSimulation.DailyProcessToCreateHistory
            @StartDate = ''20160101'', @EndDate = ''20160102'',
            @AverageNumberOfCustomerOrdersPerDay = 60, @SaturdayPercentageOfNormalWorkDay = 50,
            @SundayPercentageOfNormalWorkDay = 0, @UpdateCustomFields = 0,
            @IsSilentMode = 1, @AreDatesPrinted = 0;'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

EXEC @ReturnCode = msdb.dbo.sp_add_jobstep @job_id = @jobId,
        @step_name = N'Export stock snapshot',
        @subsystem = N'CmdExec',
        @command = N'"C:\WWI\Jobs\WWI.Legacy.Jobs.exe" --job NightlyStockSnapshot'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

EXEC @ReturnCode = msdb.dbo.sp_add_jobschedule @job_id = @jobId, @name = N'Nightly 01:00',
        @freq_type = 4, @freq_interval = 1, @active_start_time = 10000
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

EXEC @ReturnCode = msdb.dbo.sp_add_jobserver @job_id = @jobId, @server_name = N'(local)'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

COMMIT TRANSACTION
GOTO EndSave
QuitWithRollback:
    IF (@@TRANCOUNT > 0) ROLLBACK TRANSACTION
EndSave:
GO

-- Weekly cold-room report mailed to the warehouse team.
EXEC msdb.dbo.sp_add_job @job_name = N'WWI - Weekly Cold Room Report', @enabled = 1
GO
EXEC msdb.dbo.sp_add_jobstep @job_name = N'WWI - Weekly Cold Room Report',
        @step_name = N'Mail report',
        @subsystem = N'TSQL',
        @database_name = N'WideWorldImporters',
        @command = N'EXEC msdb.dbo.sp_send_dbmail @profile_name = ''WWI'',
            @recipients = ''warehouse@wideworldimporters.com'',
            @query = ''SELECT TOP 100 * FROM WideWorldImporters.Warehouse.ColdRoomTemperatures_Archive'''
GO
