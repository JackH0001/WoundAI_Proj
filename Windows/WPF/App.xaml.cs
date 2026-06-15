using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using System.Windows;
using WoundMeasurement.AI.Modules;
using WoundMeasurement.Capture.Modules;
using WoundMeasurement.Core.Interfaces;
using WoundMeasurement.Core.Services;
using WoundMeasurement.Measurement.Modules;
using WoundMeasurement.Processing.Modules;

namespace WoundMeasurement.WPF
{
    /// <summary>
    /// App.xaml 的互動邏輯
    /// </summary>
    public partial class App : Application
    {
        private IHost? _host;

        protected override async void OnStartup(StartupEventArgs e)
        {
            try
            {
                // 建立主機
                _host = CreateHostBuilder(e.Args).Build();

                // 啟動主機
                await _host.StartAsync();

                // 設定主視窗的 DataContext
                if (MainWindow != null)
                {
                    MainWindow.DataContext = _host.Services.GetRequiredService<MainViewModel>();
                }

                base.OnStartup(e);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"應用程式啟動失敗: {ex.Message}", "錯誤", MessageBoxButton.OK, MessageBoxImage.Error);
                Shutdown();
            }
        }

        protected override async void OnExit(ExitEventArgs e)
        {
            try
            {
                if (_host != null)
                {
                    await _host.StopAsync();
                    _host.Dispose();
                }
            }
            catch (Exception ex)
            {
                // 記錄錯誤但不阻止應用程式關閉
                System.Diagnostics.Debug.WriteLine($"關閉應用程式時發生錯誤: {ex.Message}");
            }

            base.OnExit(e);
        }

        private static IHostBuilder CreateHostBuilder(string[] args) =>
            Host.CreateDefaultBuilder(args)
                .ConfigureServices((context, services) =>
                {
                    // 註冊核心服務
                    services.AddSingleton<WoundMeasurementSystem>();

                    // 註冊捕捉模組
                    services.AddTransient<ICaptureModule, OpenCVCaptureModule>();

                    // 註冊處理模組 (OpenCV 真實實作)
                    services.AddTransient<IProcessingModule, OpenCVProcessingModule>();

                    // 註冊 AI 模組 (ONNX Runtime 真實實作)
                    services.AddTransient<IAIModule, OnnxAIModule>();

                    // 註冊量測模組 (OpenCV 真實實作)
                    services.AddTransient<IMeasurementModule, OpenCVMeasurementModule>();

                    // 註冊 ViewModels
                    services.AddTransient<MainViewModel>();
                    services.AddTransient<CaptureViewModel>();
                    services.AddTransient<ProcessingViewModel>();
                    services.AddTransient<MeasurementViewModel>();

                    // 註冊 Views
                    services.AddTransient<MainWindow>();
                    services.AddTransient<CaptureView>();
                    services.AddTransient<ProcessingView>();
                    services.AddTransient<MeasurementView>();
                })
                .ConfigureLogging((context, logging) =>
                {
                    logging.ClearProviders();
                    logging.AddConsole();
                    logging.AddDebug();
                    logging.SetMinimumLevel(LogLevel.Information);
                });
    }

}
