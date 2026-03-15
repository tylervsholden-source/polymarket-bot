from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """Tüm stratejiler bu sınıftan türetilir."""

    @abstractmethod
    def position_size(self, edge: float, price: float, capital: float) -> float:
        """
        Verilecek pozisyonun dolar büyüklüğünü hesaplar.

        Args:
            edge: AI tahmini - piyasa fiyatı (0.05 → %5 avantaj)
            price: Mevcut market fiyatı (0.0 - 1.0)
            capital: Kullanılabilir sermaye

        Returns:
            Pozisyon büyüklüğü (dolar cinsinden)
        """
        ...

    @abstractmethod
    def should_enter(self, edge: float, **kwargs) -> bool:
        """Pozisyona girilip girilmeyeceğini belirler."""
        ...
